"""scripts/compare_scoring_models.py — the metrics, and the whole run against a fake Claude client."""
import asyncio
import importlib.util
import pathlib
import types

spec = importlib.util.spec_from_file_location(
    "compare_scoring_models", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "compare_scoring_models.py")
cmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmp)

DIMS = [{"key": "greet", "name": "Greeting", "description": "", "guidance": "names the company", "weight": 50},
        {"key": "solve", "name": "Solving", "description": "", "guidance": "fixes the issue", "weight": 50}]
TRANSCRIPT = "მომხმარებელი: გამარჯობა\nოპერატორი: გამარჯობა, კომპანია ნიკორა, გისმენთ"
SUMMARY = {"language": "ka", "short_summary": "ზარი", "key_points": [], "action_items": [], "participants": [],
           "calls": [{"index": 0, "title": "t", "summary": "s", "outcome": "o"}]}


def scores(a, b, quote="გისმენთ", rationale="კარგი მისალმება"):
    ev = [{"quote": quote, "segments": [1]}]
    return {"operator_speaker": "speaker_1", "scores": [
        {"key": "greet", "score": a, "rationale": rationale, "evidence": ev},
        {"key": "solve", "score": b, "rationale": rationale, "evidence": []}]}


class FakeClient:
    """Answers like the SDK's streaming client: scoring from `answers[model]`, summaries from SUMMARY."""
    def __init__(self, answers):
        self.messages = self
        self.answers = answers

    def stream(self, **kw):
        tool = kw["tools"][0]["name"]
        payload = self.answers[kw["model"]] if tool == "submit_scores" else SUMMARY
        msg = types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input=payload)],
                                    usage=types.SimpleNamespace(input_tokens=1000, output_tokens=200))

        class Ctx:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False

            async def get_final_message(self_):
                return msg
        return Ctx()


def test_small_metrics():
    assert cmp.georgian_ratio("გამარჯობა") == 1.0 and cmp.georgian_ratio("hello") == 0.0
    assert cmp.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert cmp.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert cmp.spearman([1, 2], [1, 2]) is None            # too few cases to mean anything


def test_grade_catches_invented_quotes_bad_indices_and_missing_dimensions():
    dims = cmp.scoring.normalize_dimensions(DIMS)
    segs, timeline = cmp.scoring._timeline_for(TRANSCRIPT, None)
    ok = cmp.grade(scores(80, 70), dims, timeline, len(segs), 1.0)
    assert ok["ok"] and ok["quotes_ok"] == ok["quotes"] == 1 and ok["ka_ok"] is True
    fake = cmp.grade(scores(80, 70, quote="ეს არ ითქვა"), dims, timeline, len(segs), 1.0)
    assert fake["quotes_ok"] == 0
    english = cmp.grade(scores(80, 70, rationale="good greeting"), dims, timeline, len(segs), 1.0)
    assert english["ka_ok"] is False                       # Georgian transcript, English rationale
    missing = scores(80, 70)
    missing["scores"].pop()
    assert cmp.grade(missing, dims, timeline, len(segs), 1.0)["ok"] is False
    assert cmp.grade(None, dims, timeline, len(segs), 1.0)["ok"] is False


def test_whole_run_ranks_a_close_model_above_a_far_one():
    cases = {c["id"]: cmp.prepare(c) for c in (
        {"id": f"c{i}", "transcript": TRANSCRIPT, "config": {"dimensions": DIMS, "version": 1},
         "human": {"greet": 80 + i, "solve": 60 + 5 * i}} for i in range(5))}
    client = FakeClient({"ref": scores(80, 60), "near": scores(82, 62), "far": scores(20, 100)})
    models = ["ref", "near", "far"]

    async def go():
        sem = asyncio.Semaphore(4)
        jobs = [cmp.run_one(client, sem, m, c, rep, "score") for c in cases.values() for m in models for rep in range(2)]
        jobs += [cmp.run_one(client, sem, m, c, 0, "summary") for c in cases.values() for m in models]
        return await asyncio.gather(*jobs)

    runs = asyncio.run(go())
    assert not [r for r in runs if r["error"]]
    rows = {r["model"]: r for r in cmp.summarise_results([r for r in runs if r["kind"] == "score"], models, cases)}
    assert rows["near"]["mae_ref"] == 2 and rows["far"]["mae_ref"] == 50      # |20-80| and |100-60|, averaged
    assert rows["near"]["mae_human"] < rows["far"]["mae_human"]
    assert rows["ref"]["valid_pct"] == 100 and rows["near"]["run_sd"] == 0
    assert "short_summary" in cmp.summaries_md(runs, models, cases).lower() or "ზარი" in cmp.summaries_md(runs, models, cases)
