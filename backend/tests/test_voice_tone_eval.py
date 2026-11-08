"""scripts/voice_tone_eval.py — label mapping, weighted sampling, the metrics, and the sheet round trip."""
import importlib.util
import json
import pathlib
import random

import pytest

spec = importlib.util.spec_from_file_location(
    "voice_tone_eval", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "voice_tone_eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_every_sidecar_word_maps_to_a_class_and_unknown_to_none():
    assert ev.model_class("ANGRY") == "upset"
    assert ev.model_class("hap") is None          # the sidecar normalises; raw ids are not ours
    assert ev.model_class("happy") == "positive"
    assert ev.model_class("unknown") is None
    assert set(ev.MODEL_CLASS.values()) == set(ev.CLASSES)


@pytest.mark.parametrize("cell,want", [
    ("upset", "upset"), ("  Neutral ", "neutral"),
    (dict(ev.REVIEW_OPTIONS)["sad"], "sad"),            # the dropdown text itself
    ("გაბრაზებული", "upset"), ("angry", "upset"), ("can't tell", "unclear"),
    ("", None), (None, None), ("maybe?", None),
])
def test_review_cell_is_read_whatever_way_it_was_typed(cell, want):
    assert ev.review_class(cell) == want


def test_sampling_takes_all_of_a_rare_class_and_weights_back_to_the_population():
    turns = [{"cls": "neutral", "k": i} for i in range(1000)] + [{"cls": "upset", "k": i} for i in range(30)]
    picked = ev.stratified_sample(turns, {"neutral": 100, "upset": 80}, random.Random(1))
    neutral = [p for p in picked if p["stratum"] == "neutral"]
    upset = [p for p in picked if p["stratum"] == "upset"]
    assert len(neutral) == 100 and len(upset) == 30
    assert neutral[0]["weight"] == 10 and upset[0]["weight"] == 1
    # weighted, the sample stands for the whole population
    assert sum(p["weight"] for p in picked) == pytest.approx(1030)


def test_an_always_neutral_model_scores_high_agreement_but_zero_kappa():
    pairs = ([{"human": "neutral", "model": "neutral", "weight": 1, "confidence": .9}] * 90
             + [{"human": "upset", "model": "neutral", "weight": 1, "confidence": .9}] * 10)
    r = ev.score(pairs)
    assert r["agreement"] == pytest.approx(.9)
    assert r["baseline_neutral"] == pytest.approx(.9)
    assert r["kappa"] == pytest.approx(0)
    assert r["upset"][0]["flagged"] == 0 and r["upset"][0]["recall"] == 0


def test_upset_precision_is_raw_and_recall_is_weighted():
    pairs = ([{"human": "upset", "model": "upset", "weight": 1, "confidence": .95}] * 6
             + [{"human": "neutral", "model": "upset", "weight": 1, "confidence": .55}] * 4
             # 5 sampled neutral turns stand for 50 each; one of them the listener heard as upset
             + [{"human": "upset", "model": "neutral", "weight": 10, "confidence": .8}]
             + [{"human": "neutral", "model": "neutral", "weight": 10, "confidence": .8}] * 4)
    r = ev.score(pairs)
    at0 = r["upset"][0]
    assert at0["flagged"] == 10 and at0["correct"] == 6 and at0["precision"] == pytest.approx(.6)
    assert at0["recall"] == pytest.approx(6 / 16)            # 6 caught of 6 + 10 heard
    lo, hi = at0["precision_ci"]
    assert lo < .6 < hi
    at9 = next(x for x in r["upset"] if x["threshold"] == .9)
    assert at9["flagged"] == 6 and at9["precision"] == 1.0   # the unsure ones drop out


def test_wilson_handles_the_edges():
    assert ev.wilson(0, 0) is None
    lo, hi = ev.wilson(10, 10)
    assert hi == 1.0 and 0.6 < lo < 1


def test_sheet_round_trip_and_report(tmp_path):
    rows = [{"n": n, "clip": f"{n:04d}.mp3", "start": 0.0, "end": 2.5} for n in (1, 2, 3, 4)]
    ev.write_review_xlsx(tmp_path / "review.xlsx", rows)
    from openpyxl import load_workbook
    wb = load_workbook(tmp_path / "review.xlsx")
    ws = wb["Review"]
    header = [c.value for c in ws[1]]
    assert "label" not in " ".join(str(h) for h in header).lower()   # blind: no model answer column
    opts = dict(ev.REVIEW_OPTIONS)
    ws["D2"], ws["D3"], ws["D4"] = opts["upset"], "neutral", opts["unclear"]
    wb.save(tmp_path / "review.xlsx")

    key = {"meta": {"tenant": "demo", "model": "m", "calls": 1, "min_seconds": 1.0},
           "items": [{"n": 1, "label": "angry", "confidence": .9, "weight": 1},
                     {"n": 2, "label": "angry", "confidence": .6, "weight": 1},
                     {"n": 3, "label": "neutral", "confidence": .9, "weight": 1},
                     {"n": 4, "label": "sad", "confidence": .9, "weight": 1}]}
    (tmp_path / "model.json").write_text(json.dumps(key), encoding="utf-8")
    report = ev.score_dir(tmp_path)
    assert "Reviewed and usable: **2** turns" in report
    assert "blank: 1, can't tell: 1" in report
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == report


def test_an_empty_review_says_so(tmp_path):
    ev.write_review_xlsx(tmp_path / "review.xlsx", [{"n": 1, "clip": "0001.mp3", "start": 0, "end": 1}])
    (tmp_path / "model.json").write_text(json.dumps(
        {"meta": {}, "items": [{"n": 1, "label": "angry", "confidence": .9, "weight": 1}]}), encoding="utf-8")
    assert "Nothing to score yet" in ev.score_dir(tmp_path)
