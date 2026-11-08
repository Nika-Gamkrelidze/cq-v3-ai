"""Compare Claude models on the app's REAL scoring and summary prompts.

Why: the analysers run on one model (today Opus 4.8) and are most of the cost of a call. Whether a
cheaper model does the job on Georgian calls is a quality question, and "it looks fine on two
examples" is not an answer. This sends the SAME system prompt, tool and transcript the product
sends (`scoring._build_system`, `scoring.SCORE_TOOL`, `summarise._SYSTEM`, ...) to each model and
reports what can be measured, then writes the summaries side by side for a Georgian reader.

    docker compose run --rm -T -e ANTHROPIC_API_KEY -v "$PWD/backend:/app" -v "$PWD:/data" api \\
        python scripts/compare_scoring_models.py /data/cases.jsonl \\
        --models claude-opus-4-8,claude-sonnet-5-5,claude-haiku-4-5-20251001 --reps 2 --out /data/compare

The FIRST model is the reference the others are compared with. It needs an Anthropic key in
ANTHROPIC_API_KEY (env only — nothing is read from or written to the database). Cost: about one
product call per case x model x rep, roughly $0.10 each on Opus, so 20 cases x 3 models x 2 reps is
a few dollars.

cases.jsonl — one JSON object per line:
    {"id": "...", "transcript": "...", "segments": [{"start":0,"end":3.1,"speaker":"speaker_0","text":"..."}],
     "language": "ka", "config": {"dimensions": [...], "rubric": "...", "version": 3},
     "reference": {"<dimension key>": 80},      # optional: the scores already stored (production model)
     "human":     {"<dimension key>": 75}}      # optional: a reviewer's corrected scores — the real truth

Writes <out>.json (every raw run), <out>.md (the table) and <out>_summaries.md (side by side).
Quality signals, in order of how much they matter:
  * vs human   — mean absolute error against reviewer-corrected scores (needs "human" in cases)
  * vs ref     — how far from the reference model; a cheaper model that agrees with it is a candidate
  * valid%     — every dimension scored with an integer 0-100 (a model that breaks the schema is out)
  * quotes%    — evidence quotes that really are in the transcript (a model that invents quotes is out)
  * ka%        — rationales written in Georgian for a Georgian transcript (the prompt demands it)
  * run SD     — how much one model disagrees with itself between repeats
Agreement is not accuracy: only "vs human" says who is RIGHT. Read the summaries yourself.
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys

sys.path.insert(0, "/app")

import anthropic                                           # noqa: E402
from app.services import scoring, summarise                 # noqa: E402

# USD per million tokens (input, output), list prices from Anthropic's pricing page, 2026-11-03.
# A model missing here is still run; its cost column just says n/a.
PRICES = {
    "claude-opus-4-8": (5, 25), "claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10),
    "claude-sonnet-5": (2, 10), "claude-haiku-4-5-20251001": (1, 5),
}


# ---------------------------------------------------------------- pure metrics (unit-tested)
def norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def georgian_ratio(text: str) -> float:
    letters = [c for c in text or "" if c.isalpha()]
    return sum("ა" <= c <= "ჿ" for c in letters) / len(letters) if letters else 0.0


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 4 or len(a) != len(b):
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def grade(found: dict | None, model_dims: list[dict], timeline: str, seg_count: int,
          transcript_ka: float) -> dict:
    """What one scoring answer is worth, judged only against the transcript it was given."""
    out = {"ok": False, "scores": {}, "quotes": 0, "quotes_ok": 0, "idx": 0, "idx_ok": 0, "ka_ok": None}
    if not found:
        return out
    text = norm(timeline)
    rationale = []
    for s in found.get("scores") or []:
        if not isinstance(s, dict):
            continue
        key, score = str(s.get("key", "")).strip(), s.get("score")
        if isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100:
            out["scores"][key] = score
        rationale.append(str(s.get("rationale") or ""))
        for ev in s.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            out["quotes"] += 1
            out["quotes_ok"] += bool(norm(ev.get("quote"))) and norm(ev.get("quote")) in text
            for i in ev.get("segments") or []:
                out["idx"] += 1
                out["idx_ok"] += isinstance(i, int) and 0 <= i < seg_count
    out["ok"] = all(d["key"] in out["scores"] for d in model_dims)
    if transcript_ka >= 0.3:
        out["ka_ok"] = georgian_ratio(" ".join(rationale)) >= 0.5
    return out


def weighted_total(scores: dict, dims: list[dict]) -> float | None:
    parts = [(scores[d["key"]], d["weight"]) for d in dims if d["key"] in scores]
    w = sum(p[1] for p in parts)
    return sum(s * wt for s, wt in parts) / w if w else None


def summarise_results(runs: list[dict], models: list[str], cases: dict) -> list[dict]:
    """One row per model. `runs`: {case, model, rep, grade, tin, tout, error}."""
    def mean_scores(model, cid):
        rs = [r["grade"]["scores"] for r in runs if r["model"] == model and r["case"] == cid and r["grade"]["ok"]]
        keys = rs[0].keys() if rs else []
        return {k: statistics.fmean(r[k] for r in rs) for k in keys}

    def mae(model, cid, other):
        mine = mean_scores(model, cid)
        diffs = [abs(mine[k] - v) for k, v in (other or {}).items() if k in mine]
        return diffs

    rows = []
    for m in models:
        mine = [r for r in runs if r["model"] == m]
        g = [r["grade"] for r in mine]
        q, qo = sum(x["quotes"] for x in g), sum(x["quotes_ok"] for x in g)
        ka = [x["ka_ok"] for x in g if x["ka_ok"] is not None]
        ref_d, hum_d, ref_prod_d, sds = [], [], [], []
        for cid, c in cases.items():
            ref_d += mae(m, cid, mean_scores(models[0], cid)) if m != models[0] else []
            ref_prod_d += mae(m, cid, c.get("reference"))
            hum_d += mae(m, cid, c.get("human"))
            per_key = {}
            for r in mine:
                if r["case"] == cid and r["grade"]["ok"]:
                    for k, v in r["grade"]["scores"].items():
                        per_key.setdefault(k, []).append(v)
            sds += [statistics.pstdev(v) for v in per_key.values() if len(v) > 1]
        tot_m, tot_r = [], []
        for cid, c in cases.items():
            a, b = weighted_total(mean_scores(m, cid), c["_dims"]), weighted_total(mean_scores(models[0], cid), c["_dims"])
            if a is not None and b is not None:
                tot_m.append(a)
                tot_r.append(b)
        tin = statistics.fmean([r["tin"] for r in mine if r["tin"]] or [0])
        tout = statistics.fmean([r["tout"] for r in mine if r["tout"]] or [0])
        price = PRICES.get(m)
        rows.append({
            "model": m, "runs": len(mine), "errors": sum(1 for r in mine if r.get("error")),
            "valid_pct": 100 * sum(x["ok"] for x in g) / len(g) if g else None,
            "quotes_pct": 100 * qo / q if q else None,
            "ka_pct": 100 * sum(ka) / len(ka) if ka else None,
            "mae_human": statistics.fmean(hum_d) if hum_d else None,
            "mae_prod": statistics.fmean(ref_prod_d) if ref_prod_d else None,
            "mae_ref": statistics.fmean(ref_d) if ref_d else None,
            "rho_ref": spearman(tot_m, tot_r) if m != models[0] else None,
            "run_sd": statistics.fmean(sds) if sds else None,
            "tin": tin, "tout": tout,
            "cost": (tin * price[0] + tout * price[1]) / 1e6 if price else None,
        })
    return rows


# ---------------------------------------------------------------- calling the models
async def ask(client, model: str, system: str, user: str, tool: dict, max_tokens: int):
    """The product's call: forced tool use, streamed (see llm_anthropic.call_tool)."""
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, tools=[tool],
                  tool_choice={"type": "tool", "name": tool["name"]},
                  messages=[{"role": "user", "content": user}])
    async with client.messages.stream(**kwargs) as st:
        msg = await st.get_final_message()
    found = next((dict(b.input) for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    u = msg.usage
    tin = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
        + (getattr(u, "cache_creation_input_tokens", 0) or 0)
    return found, tin, u.output_tokens or 0


async def run_one(client, sem, model: str, case: dict, rep: int, what: str) -> dict:
    out = {"case": case["id"], "model": model, "rep": rep, "kind": what, "tin": 0, "tout": 0,
           "grade": grade(None, [], "", 0, 0), "raw": None, "error": None}
    async with sem:
        try:
            if what == "score":
                found, out["tin"], out["tout"] = await ask(
                    client, model, scoring._build_system(case["config"], case["_model_dims"]),
                    f"<transcript>\n{case['_timeline']}\n</transcript>", scoring.SCORE_TOOL,
                    scoring.output_budget(len(case["_model_dims"])))
                out["grade"] = grade(found, case["_model_dims"], case["_timeline"], len(case["_segs"]), case["_ka"])
                out["raw"] = found
            else:
                call = {"transcript": case["transcript"], "segments": case.get("segments"),
                        "language": case.get("language"), "filename": case["id"]}
                found, out["tin"], out["tout"] = await ask(
                    client, model, summarise._SYSTEM,
                    summarise._ONE_CALL_INTRO + summarise.render_call(0, call),
                    summarise.SUMMARY_TOOL, summarise.output_budget(1))
                out["raw"] = summarise.normalise(found, 1) if found else None
        except Exception as exc:  # noqa: BLE001 — one bad call must not lose the other results
            out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out


def prepare(case: dict) -> dict:
    config = case["config"]
    dims = scoring.normalize_dimensions(config.get("dimensions"))
    segs, timeline = scoring._timeline_for(case["transcript"], case.get("segments"))
    case.update(_dims=dims, _model_dims=[d for d in dims if not d.get("source")], _segs=segs,
                _timeline=timeline, _ka=georgian_ratio(case["transcript"]))
    return case


def fmt(v, spec="{:.1f}", none="–"):
    return none if v is None else spec.format(v)


def table(rows: list[dict], has_human: bool) -> str:
    head = ["model", "runs (err)", "valid%", "quotes%", "ka%", "vs human" if has_human else "vs prod", "vs ref", "rank ρ", "run SD",
            "tokens in/out", "$ / call (score)"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        mae = r["mae_human"] if has_human else r["mae_prod"]
        lines.append("| " + " | ".join([
            r["model"], f"{r['runs']} ({r['errors']})", fmt(r["valid_pct"], "{:.0f}"), fmt(r["quotes_pct"], "{:.0f}"),
            fmt(r["ka_pct"], "{:.0f}"), fmt(mae), fmt(r["mae_ref"]), fmt(r["rho_ref"], "{:.2f}"), fmt(r["run_sd"]),
            f"{r['tin']:.0f} / {r['tout']:.0f}", fmt(r["cost"], "${:.4f}", "n/a")]) + " |")
    return "\n".join(lines)


def summaries_md(runs: list[dict], models: list[str], cases: dict) -> str:
    out = ["# Summaries side by side — read these in Georgian and mark which one you would send\n"]
    for cid in cases:
        out.append(f"\n## {cid}\n")
        for m in models:
            r = next((x for x in runs if x["kind"] == "summary" and x["case"] == cid and x["model"] == m and x["raw"]), None)
            out.append(f"### {m}\n")
            if not r:
                out.append("_no summary_\n")
                continue
            s = r["raw"]
            out.append(f"{s.get('short_summary', '')}\n")
            out += [f"- {p}" for p in s.get("key_points", [])]
            if s.get("action_items"):
                out.append("\nAction items:")
                out += [f"- {p}" for p in s["action_items"]]
            out.append("")
    return "\n".join(out)


async def main(args) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("Set ANTHROPIC_API_KEY (docker compose run -e ANTHROPIC_API_KEY ...).")
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = {}
    for line in open(args.cases, encoding="utf8"):
        if line.strip():
            c = prepare(json.loads(line))
            cases[c["id"]] = c
            if args.limit and len(cases) >= args.limit:
                break
    if not cases or len(models) < 2:
        print("Need at least one case and two models.")
        return 2
    client, sem = anthropic.AsyncAnthropic(api_key=key), asyncio.Semaphore(args.concurrency)
    jobs = [run_one(client, sem, m, c, rep, "score") for c in cases.values() for m in models for rep in range(args.reps)]
    jobs += [run_one(client, sem, m, c, 0, "summary") for c in cases.values() for m in models]
    print(f"{len(cases)} cases x {len(models)} models: {len(jobs)} calls ...", flush=True)
    runs = await asyncio.gather(*jobs)
    scored = [r for r in runs if r["kind"] == "score"]
    has_human = any(c.get("human") for c in cases.values())
    rows = summarise_results(scored, models, cases)
    md = table(rows, has_human)
    with open(args.out + ".json", "w", encoding="utf8") as f:
        json.dump({"models": models, "runs": runs, "rows": rows}, f, ensure_ascii=False, indent=1)
    with open(args.out + ".md", "w", encoding="utf8") as f:
        f.write(md + "\n")
    with open(args.out + "_summaries.md", "w", encoding="utf8") as f:
        f.write(summaries_md(runs, models, cases))
    print(md)
    errs = [r for r in runs if r["error"]]
    for r in errs[:5]:
        print("error:", r["model"], r["case"], r["error"])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases")
    ap.add_argument("--models", required=True, help="comma list; the first is the reference")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="compare")
    sys.exit(asyncio.run(main(ap.parse_args())))
