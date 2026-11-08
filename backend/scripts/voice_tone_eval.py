"""Measure the voice-tone model against what a person hears.

Why: the voice half of the Sentiment analyser (`cq-sentiment`, today
`superb/wav2vec2-base-superb-er`) was trained on ~12 hours of ACTED, studio-recorded ENGLISH
speech with four labels. Our calls are real, Georgian and on the telephone. Nobody has checked
whether its red patches on the timeline mean anything there, and "it looks plausible on two
calls" is not an answer. This script produces a blind listening test and scores it.

Two steps, run where the recordings and the sidecar live (the server, inside the api image):

  1. export — picks real recordings of ONE workspace, asks the sidecar for each turn's voice
     tone exactly as the product does, samples turns, cuts each sampled turn to its own clip
     and writes:
         <out>/review.xlsx   the listening sheet: clip number + a dropdown. NO model answers.
         <out>/clips/        one mp3 per row of the sheet, in shuffled order
         <out>/model.json    the answer key (the model's labels, recordings, times). Keep it
                             away from the reviewer — seeing the model's answer first is the
                             one thing that makes this measurement worthless.
  2. score  — reads the filled review.xlsx beside model.json and reports how often the model
     agrees with the person, in <out>/report.md.

    cd /home/cqdeploy/cq-v3-ai && mkdir -p voice_eval
    docker compose -p cqv3 run --rm -T -v "$PWD/backend:/app:z" -v "$PWD/voice_eval:/out:z" api \\
        python scripts/voice_tone_eval.py export --tenant <slug or uuid> --calls 25 --out /out/run1
    # copy voice_eval/run1/review.xlsx + clips/ to the reviewer; they fill column D; copy it back
    docker compose -p cqv3 run --rm -T -v "$PWD/backend:/app:z" -v "$PWD/voice_eval:/out:z" api \\
        python scripts/voice_tone_eval.py score /out/run1

Read-only on the product: one SELECT on `audio_jobs` (always filtered by `client_id`), reads of
the stored audio, calls to the sidecar's `/prosody/segments`. Nothing is written to the database
(no stored analysis is overwritten, no usage row, no unit spent) and no AI provider is called.

SAMPLING. A call is mostly neutral turns, so a plain random sample would hold almost no turns
the model calls angry, and its precision — "when it paints red, is it right?" — could not be
measured. Turns are therefore sampled per MODEL label (all of a rare label, up to a cap) and
every reviewed turn carries a weight = turns-with-that-label / turns-sampled-from-it, so the
reported agreement and recall are what they would be over ALL eligible turns, not over the
oversampled mix. Only turns of at least --min-seconds are eligible: a person cannot judge half
a second, so the result describes turns of that length and longer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# The four classes a person and the model are compared in. The sidecar's own vocabulary is
# wider than this checkpoint's four labels; every word it can say maps to one class here.
CLASSES = ("neutral", "positive", "upset", "sad")
MODEL_CLASS = {
    "neutral": "neutral", "calm": "neutral", "other": "neutral",
    "happy": "positive", "excited": "positive", "surprised": "positive",
    "angry": "upset", "frustrated": "upset", "disgusted": "upset", "fearful": "upset",
    "sad": "sad",
}
# What the reviewer picks from. The key before " — " is what is read back.
REVIEW_OPTIONS = (
    ("neutral", "ნეიტრალური / მშვიდი — neutral, calm"),
    ("positive", "დადებითი / მხიარული — positive, cheerful"),
    ("upset", "გაღიზიანებული / გაბრაზებული / დაძაბული — upset, angry, tense"),
    ("sad", "მოწყენილი / დაღლილი — sad, tired"),
    ("unclear", "ვერ ვარჩევ — can't tell"),
)
DEFAULT_TARGETS = {"neutral": 120, "positive": 60, "upset": 80, "sad": 60}
THRESHOLDS = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9)


# --------------------------------------------------------------------------- #
# Pure parts (unit-tested in tests/test_voice_tone_eval.py)
# --------------------------------------------------------------------------- #
def model_class(label) -> str | None:
    """The class of one sidecar label, or None for "unknown" (a turn too short to read)."""
    return MODEL_CLASS.get(str(label or "").strip().lower())


def review_class(text) -> str | None:
    """A reviewer's cell -> class | "unclear" | None (blank or unreadable). Accepts the dropdown
    text, the bare key, or the Georgian/English words, so a hand-typed answer still counts."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    for key, label in REVIEW_OPTIONS:
        if s in (key, label.lower()):
            return key
    for key, label in REVIEW_OPTIONS:
        words = {w.strip() for part in label.lower().replace("—", "/").split("/")
                 for w in part.split(",")}
        if s in words - {""}:
            return key
    return None


def stratified_sample(turns: list[dict], targets: dict, rng: random.Random) -> list[dict]:
    """Pick turns per model class, each carrying `weight` = population / taken for its class.
    `turns` items need `cls`. Returns the picked turns (copies), not yet shuffled."""
    picked = []
    for cls in CLASSES:
        pool = [t for t in turns if t["cls"] == cls]
        if not pool:
            continue
        take = min(len(pool), max(0, int(targets.get(cls, 0))))
        if take == 0:
            continue
        weight = len(pool) / take
        for t in rng.sample(pool, take):
            picked.append({**t, "stratum": cls, "weight": weight})
    return picked


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for k successes in n, or None when n is 0."""
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def score(pairs: list[dict]) -> dict:
    """Metrics over reviewed pairs `{"human", "model", "weight", "confidence"}` where human and
    model are classes (the caller has already dropped blanks and "can't tell").

    Weighted (= estimated over all eligible turns): agreement, the always-"neutral" baseline,
    Cohen's kappa, upset recall and F1. Precision of "upset" is computed on raw counts: every
    turn the model called upset sits in one stratum with one weight, so weighting changes
    nothing there and the raw count gives an honest interval."""
    W = sum(p["weight"] for p in pairs)
    matrix = {h: {m: 0.0 for m in CLASSES} for h in CLASSES}
    raw = {h: {m: 0 for m in CLASSES} for h in CLASSES}
    for p in pairs:
        matrix[p["human"]][p["model"]] += p["weight"]
        raw[p["human"]][p["model"]] += 1
    out = {"n": len(pairs), "matrix": matrix, "raw": raw}
    if not pairs or W <= 0:
        return out
    agree = sum(matrix[c][c] for c in CLASSES) / W
    human_tot = {h: sum(matrix[h].values()) for h in CLASSES}
    model_tot = {m: sum(matrix[h][m] for h in CLASSES) for m in CLASSES}
    expected = sum(human_tot[c] * model_tot[c] for c in CLASSES) / (W * W)
    out.update(
        agreement=agree,
        baseline_neutral=human_tot["neutral"] / W,
        kappa=(agree - expected) / (1 - expected) if expected < 1 else None,
        human_share={c: human_tot[c] / W for c in CLASSES},
        model_share={c: model_tot[c] / W for c in CLASSES},
    )
    rows = []
    for t in THRESHOLDS:
        flagged = [p for p in pairs if p["model"] == "upset" and (p.get("confidence") or 0) >= t]
        tp_raw = sum(1 for p in flagged if p["human"] == "upset")
        tp_w = sum(p["weight"] for p in flagged if p["human"] == "upset")
        human_upset = human_tot["upset"]
        precision = tp_raw / len(flagged) if flagged else None
        recall = tp_w / human_upset if human_upset > 0 else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None and precision + recall > 0 else None)
        rows.append({"threshold": t, "flagged": len(flagged), "correct": tp_raw,
                     "precision": precision, "precision_ci": wilson(tp_raw, len(flagged)),
                     "recall": recall, "f1": f1})
    out["upset"] = rows
    return out


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def render_report(meta: dict, result: dict, skipped: dict) -> str:
    """The markdown report: plain sentences first, tables after."""
    L = []
    L.append(f"# Voice tone vs. a listener — {meta.get('tenant', '')}")
    L.append("")
    L.append(f"Model `{meta.get('model', '?')}`, exported {meta.get('exported_at', '?')}, "
             f"{meta.get('calls', 0)} recordings, turns of at least {meta.get('min_seconds')} s.")
    L.append(f"Reviewed and usable: **{result['n']}** turns "
             f"(blank: {skipped.get('blank', 0)}, can't tell: {skipped.get('unclear', 0)}).")
    L.append("")
    if not result.get("n"):
        L.append("Nothing to score yet — fill column D of review.xlsx first.")
        return "\n".join(L) + "\n"
    a, b, k = result["agreement"], result["baseline_neutral"], result.get("kappa")
    L.append("## In one paragraph")
    L.append("")
    kappa = "—" if k is None else f"{k:.2f}"
    L.append(f"The model and the listener agree on **{_pct(a)}** of turns. A model that answered "
             f"\"neutral\" every time would score **{_pct(b)}**, so agreement alone flatters it; "
             f"Cohen's kappa, which discounts chance, is **{kappa}** "
             f"(0 = no better than chance, 0.4 = moderate, 0.6+ = substantial).")
    up0 = result["upset"][0]
    if up0["flagged"]:
        lo, hi = up0["precision_ci"]
        L.append(f"When it paints a turn red (upset/angry), the listener agrees "
                 f"**{_pct(up0['precision'])}** of the time (95% range {_pct(lo)}–{_pct(hi)}, "
                 f"{up0['flagged']} turns). Of the turns the listener heard as upset, it catches "
                 f"**{_pct(up0['recall'])}**.")
    else:
        L.append("The model called no reviewed turn upset, so its red patches cannot be judged here.")
    L.append("")
    L.append("A reasonable bar before showing it to customers: red is right at least 7 times in 10 "
             "and catches at least half of what a person hears as upset. Below that, the colours "
             "on the timeline mislead more than they help.")
    L.append("")
    L.append("## Upset detection at a confidence threshold")
    L.append("")
    L.append("Showing red only when the model is at least this sure:")
    L.append("")
    L.append("| min confidence | turns flagged | right | precision (95% range) | recall | F1 |")
    L.append("|---|---|---|---|---|---|")
    for r in result["upset"]:
        ci = r["precision_ci"]
        rng = f" ({_pct(ci[0])}–{_pct(ci[1])})" if ci else ""
        f1 = "—" if r["f1"] is None else f"{r['f1']:.2f}"
        L.append(f"| {r['threshold']:.1f} | {r['flagged']} | {r['correct']} | "
                 f"{_pct(r['precision'])}{rng} | {_pct(r['recall'])} | {f1} |")
    L.append("")
    L.append("## How often each class occurs (estimated over all eligible turns)")
    L.append("")
    L.append("| class | listener | model |")
    L.append("|---|---|---|")
    for c in CLASSES:
        L.append(f"| {c} | {_pct(result['human_share'][c])} | {_pct(result['model_share'][c])} |")
    L.append("")
    L.append("## Confusion matrix (raw reviewed turns; rows = listener, columns = model)")
    L.append("")
    L.append("| listener \\ model | " + " | ".join(CLASSES) + " |")
    L.append("|---|" + "---|" * len(CLASSES))
    for h in CLASSES:
        L.append(f"| {h} | " + " | ".join(str(result["raw"][h][m]) for m in CLASSES) + " |")
    L.append("")
    L.append("Percentages other than precision are weighted back to the real mix of turns "
             "(rare labels were oversampled for review). Fewer than ~30 turns in a cell means "
             "that number can move a lot with more reviews.")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# The spreadsheet
# --------------------------------------------------------------------------- #
REVIEW_HEADER = ("#", "clip", "seconds", "how does it SOUND? (pick one)", "note")


def write_review_xlsx(path: Path, rows: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Review"
    ws.append(REVIEW_HEADER)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="FFE9EEF5")
    for r in rows:
        ws.append([r["n"], r["clip"], round(r["end"] - r["start"], 1), None, None])
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 9
    ws.column_dimensions["D"].width = 62
    ws.column_dimensions["E"].width = 40
    ws.freeze_panes = "A2"

    lab = wb.create_sheet("Labels")
    lab.append(["option", "when to pick it"])
    lab["A1"].font = lab["B1"].font = Font(bold=True)
    hints = {
        "neutral": "ჩვეულებრივი, მშვიდი ტონი. Ordinary, even tone.",
        "positive": "თბილი, მხიარული, კმაყოფილი. Warm, cheerful, pleased.",
        "upset": "გაბრაზებული, გაღიზიანებული, მოუთმენელი, ხმამაღალი. Angry, irritated, impatient, raised voice.",
        "sad": "მოწყენილი, დაღლილი, უიმედო. Low, tired, resigned.",
        "unclear": "ძალიან მოკლე, ხმაური, ორი ადამიანი ერთად. Too short, noise, overlap.",
    }
    for key, label in REVIEW_OPTIONS:
        lab.append([label, hints[key]])
    lab.column_dimensions["A"].width = 62
    lab.column_dimensions["B"].width = 80
    dv = DataValidation(type="list", formula1=f"=Labels!$A$2:$A${len(REVIEW_OPTIONS) + 1}",
                        allow_blank=True, showErrorMessage=False)
    ws.add_data_validation(dv)
    if rows:
        dv.add(f"D2:D{len(rows) + 1}")

    how = wb.create_sheet("How to")
    for line in (
        "ყოველ სტრიქონზე მოუსმინეთ კლიპს (clips საქაღალდე) და D სვეტში აირჩიეთ, როგორ ჟღერს.",
        "შეაფასეთ ხმა — სიმაღლე, ხმამაღლობა, ტემპი — და არა სიტყვების შინაარსი.",
        "Listen to each clip (clips folder) and pick in column D how it SOUNDS.",
        "Judge the voice — pitch, loudness, pace — not what the words say.",
        "Order is shuffled on purpose. Don't look anything up; your first impression is the data.",
    ):
        how.append([line])
    how.column_dimensions["A"].width = 110
    for row in how.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True)
    wb.save(path)


def read_review_xlsx(path: Path) -> dict[int, str | None]:
    """`{row number: class | "unclear" | None}` from a filled sheet. Finds the columns by header,
    so a reviewer who inserted a column or sorted the sheet is still read correctly."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Review"] if "Review" in wb.sheetnames else wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [str(h or "").strip().lower() for h in next(it, ())]
    try:
        n_col = header.index("#")
        a_col = next(i for i, h in enumerate(header) if h.startswith("how does it sound"))
    except (ValueError, StopIteration):
        raise SystemExit(f"{path}: no '#' / 'how does it SOUND?' header in the Review sheet")
    out = {}
    for row in it:
        if row is None or n_col >= len(row) or row[n_col] in (None, ""):
            continue
        try:
            n = int(row[n_col])
        except (TypeError, ValueError):
            continue
        out[n] = review_class(row[a_col] if a_col < len(row) else None)
    wb.close()
    return out


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def _safe_media_path(root: Path, rel: str | None) -> Path | None:
    """Same rule as `routers/recordings.py::_stored_file`: under MEDIA_ROOT, and a real file."""
    if not rel:
        return None
    root = root.resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


async def _candidates(dsn: str, tenant: str, calls: int, since: date | None, seed: int):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        client = await conn.fetchrow(
            "SELECT id, slug, name FROM clients WHERE slug = $1 OR id::text = lower($1)", tenant)
        if client is None:
            raise SystemExit(f"no workspace with slug or id {tenant!r}")
        rows = await conn.fetch(
            """SELECT id, filename, content_type, audio_path, segments, language, created_at
                 FROM audio_jobs
                WHERE client_id = $1
                  AND source = 'audio' AND audio_path IS NOT NULL
                  AND jsonb_typeof(segments) = 'array' AND jsonb_array_length(segments) > 0
                  AND ($2::date IS NULL OR created_at >= $2::date)
                ORDER BY md5(id::text || $3)
                LIMIT $4""",
            client["id"], since, str(seed), calls * 3)
    finally:
        await conn.close()
    return dict(client), [dict(r) for r in rows]


async def _tone(url: str, audio: bytes, filename: str, content_type: str | None, ranges: list[dict]):
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        resp = await client.post(url.rstrip("/") + "/prosody/segments",
                                 files={"file": (filename or "audio", audio,
                                                 content_type or "application/octet-stream")},
                                 data={"segments": json.dumps(ranges)})
        resp.raise_for_status()
        data = resp.json()
    return data.get("segments") or [], data.get("model")


def _cut(src: Path, start: float, end: float, dest: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
                    "-to", f"{end:.3f}", "-i", str(src), "-ac", "1", "-ar", "16000",
                    "-b:a", "64k", str(dest)], check=True)


async def export(args) -> None:
    from app.services.sentiment import _ranges  # the product's own slicing rule

    out = Path(args.out)
    if (out / "model.json").exists() and not args.force:
        raise SystemExit(f"{out} already holds an export; pass --force to overwrite it")
    url = args.sentiment_url or os.environ.get("SENTIMENT_URL", "")
    if not url:
        raise SystemExit("no sidecar: pass --sentiment-url or set SENTIMENT_URL")
    dsn = args.database_url or os.environ.get("DATABASE_URL", "")
    media_root = Path(args.media_root or os.environ.get("MEDIA_ROOT", "/data/media"))
    since = date.fromisoformat(args.since) if args.since else None

    client, rows = await _candidates(dsn, args.tenant, args.calls, since, args.seed)
    print(f"workspace {client['slug']}: {len(rows)} candidate recordings with audio and timings")
    turns, used, model_id = [], [], None
    for row in rows:
        if len(used) >= args.calls:
            break
        path = _safe_media_path(media_root, row["audio_path"])
        segs = row["segments"]
        if isinstance(segs, str):
            segs = json.loads(segs)
        ranges = _ranges(segs)
        if path is None or not ranges:
            continue
        try:
            items, model_id = await _tone(url, path.read_bytes(), row["filename"],
                                          row["content_type"], ranges)
        except Exception as exc:  # noqa: BLE001 — one bad file must not end the run
            print(f"  skip {row['id']}: {exc}", file=sys.stderr)
            continue
        by_i = {int(r["i"]): r for r in ranges}
        speaker = {int(s["i"]): s.get("speaker") for s in segs
                   if isinstance(s, dict) and str(s.get("i", "")).lstrip("-").isdigit()}
        n_before = len(turns)
        for it in items:
            try:
                i = int(it.get("i"))
            except (TypeError, ValueError):
                continue
            cls = model_class(it.get("label"))
            r = by_i.get(i)
            if cls is None or r is None or r["end"] - r["start"] < args.min_seconds:
                continue
            turns.append({"job_id": str(row["id"]), "i": i, "start": r["start"], "end": r["end"],
                          "speaker": speaker.get(i), "label": str(it.get("label")),
                          "confidence": it.get("confidence"), "cls": cls,
                          "audio": str(path)})
        used.append(str(row["id"]))
        print(f"  {row['id']}: {len(turns) - n_before} eligible turns")
    if not turns:
        raise SystemExit("no eligible turns — no recordings with stored audio and timings, "
                         "or the sidecar classified nothing")

    targets = dict(DEFAULT_TARGETS)
    for spec in args.target or []:
        k, _, v = spec.partition("=")
        if k not in CLASSES or not v.isdigit():
            raise SystemExit(f"--target {spec!r}: expected one of {CLASSES}=<count>")
        targets[k] = int(v)
    rng = random.Random(args.seed)
    picked = stratified_sample(turns, targets, rng)
    rng.shuffle(picked)

    (out / "clips").mkdir(parents=True, exist_ok=True)
    key = []
    for n, t in enumerate(picked, 1):
        clip = f"{n:04d}.mp3"
        _cut(Path(t["audio"]), t["start"], t["end"], out / "clips" / clip)
        t.update(n=n, clip=clip)
        key.append({k: t[k] for k in ("n", "clip", "job_id", "i", "start", "end", "speaker",
                                      "label", "confidence", "stratum", "weight")})
    population = {c: sum(1 for t in turns if t["cls"] == c) for c in CLASSES}
    meta = {"tenant": client["slug"], "client_id": str(client["id"]), "model": model_id,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "calls": len(used), "recordings": used, "min_seconds": args.min_seconds,
            "seed": args.seed, "population": population, "targets": targets,
            "sampled": {c: sum(1 for t in picked if t["stratum"] == c) for c in CLASSES}}
    (out / "model.json").write_text(json.dumps({"meta": meta, "items": key}, ensure_ascii=False,
                                               indent=1), encoding="utf-8")
    write_review_xlsx(out / "review.xlsx", picked)
    print(f"\n{len(picked)} turns from {len(used)} recordings -> {out}")
    print("eligible turns by model label:", population)
    print("sampled for review:          ", meta["sampled"])
    print("\nGive the reviewer review.xlsx and clips/ ONLY. model.json is the answer key.")


# --------------------------------------------------------------------------- #
# score
# --------------------------------------------------------------------------- #
def score_dir(out: Path) -> str:
    data = json.loads((out / "model.json").read_text(encoding="utf-8"))
    answers = read_review_xlsx(out / "review.xlsx")
    pairs, skipped = [], {"blank": 0, "unclear": 0}
    for item in data["items"]:
        human = answers.get(item["n"])
        if human is None:
            skipped["blank"] += 1
            continue
        if human == "unclear":
            skipped["unclear"] += 1
            continue
        pairs.append({"human": human, "model": model_class(item["label"]) or "neutral",
                      "weight": float(item.get("weight") or 1.0),
                      "confidence": item.get("confidence")})
    report = render_report(data["meta"], score(pairs), skipped)
    (out / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="build the blind listening test")
    ex.add_argument("--tenant", required=True, help="workspace slug or client_id")
    ex.add_argument("--calls", type=int, default=25, help="recordings to use (default 25)")
    ex.add_argument("--since", help="only recordings from this date on (YYYY-MM-DD)")
    ex.add_argument("--min-seconds", type=float, default=1.0,
                    help="shortest turn a person is asked to judge (default 1.0)")
    ex.add_argument("--target", action="append", metavar="CLASS=N",
                    help=f"turns to sample per model class (defaults {DEFAULT_TARGETS})")
    ex.add_argument("--seed", type=int, default=7)
    ex.add_argument("--out", required=True)
    ex.add_argument("--force", action="store_true", help="overwrite an existing export")
    ex.add_argument("--sentiment-url"), ex.add_argument("--database-url"), ex.add_argument("--media-root")
    sc = sub.add_parser("score", help="score a filled review.xlsx")
    sc.add_argument("out")
    args = ap.parse_args(argv)
    if args.cmd == "export":
        asyncio.run(export(args))
    else:
        print(score_dir(Path(args.out)))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
