"""cq-sentiment — acoustic emotion (prosody) over HTTP, on CPU.

Deliberately the same shape as the `cq-embeddings` sidecar: one small self-hosted model behind
one endpoint, no external API key, and the audio never leaves the deployment. That last point
is the reason this is a container and not a SaaS call — the tenants are banks and clinics.

  POST /prosody           (multipart: file)  -> {label, arousal, valence, dominance, confidence, scores}
  POST /prosody/segments  (multipart: file + form `segments` = JSON [{i, start, end}, ...])
                                             -> {segments: [{i, label, confidence, arousal, valence}], model}
  GET  /health                               -> {status, model, loaded}

WHAT IT MEASURES: how something was said, not what was said. Pitch, energy and timing carry
emotion largely independently of language, which is why this pairs well with a Georgian-heavy
call set — the text half of the signal comes from Claude, which reads Georgian directly.

MODEL CHOICE is an env var, not a hardcode, because the right model here is a licensing
decision as much as a technical one. Note for whoever changes it: the widely-cited
`audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim` is CC-BY-NC — research only, and this
is a commercial product. Default below is a permissively licensed model.

Everything is loaded lazily on the first request so the container becomes healthy immediately
and a cold model download cannot fail the whole compose up.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import tempfile
import threading

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cq-sentiment")

MODEL_ID = os.getenv("SENTIMENT_MODEL", "superb/wav2vec2-base-superb-er")
SAMPLE_RATE = 16000
MAX_SECONDS = float(os.getenv("SENTIMENT_MAX_SECONDS", "120"))

# /prosody/segments decodes a WHOLE call: the caller has already cut it into short ranges,
# so what bounds the tensor is the slicing below, not the file length. 1800 s of f32 mono
# at 16 kHz is 115 MB in memory — fine; the per-batch tensor is what the model sees.
SEGMENTS_MAX_SECONDS = float(os.getenv("SENTIMENT_SEGMENTS_MAX_SECONDS", "1800"))
MIN_SLICE_SECONDS = 0.5      # under this a slice carries no usable prosody → "unknown"
BATCH_SIZE = 16              # padded slices per forward pass
MAX_RANGES = 4000            # a 30-min call in 0.5 s slices; anything more is a bad client

app = FastAPI(title="cq-sentiment", docs_url=None, redoc_url=None)

_model = None
_extractor = None
_labels: list[str] = []
_lock = threading.Lock()

# Maps whatever label set the chosen checkpoint uses onto the vocabulary the API promises.
_ALIAS = {
    "ang": "angry", "anger": "angry", "hap": "happy", "happiness": "happy", "exc": "excited",
    "sad": "sad", "sadness": "sad", "neu": "neutral", "neutral": "neutral", "fea": "fearful",
    "fear": "fearful", "dis": "disgusted", "disgust": "disgusted", "sur": "surprised",
    "surprise": "surprised", "calm": "calm", "fru": "frustrated", "frustration": "frustrated",
}

# Rough arousal/valence coordinates per discrete emotion, used only when the checkpoint is a
# classifier (most are) rather than a dimensional regressor. Keeps the response shape stable
# so the caller never has to branch on which model is deployed.
_AV = {
    "angry":      (0.85, 0.15), "frustrated": (0.70, 0.25), "fearful":  (0.80, 0.20),
    "sad":        (0.30, 0.20), "disgusted":  (0.60, 0.20), "surprised": (0.75, 0.55),
    "happy":      (0.75, 0.90), "excited":    (0.90, 0.85), "calm":      (0.20, 0.70),
    "neutral":    (0.40, 0.50), "other":      (0.50, 0.50), "unknown":   (0.50, 0.50),
}


def _load():
    """Load the model once, under a lock so concurrent first requests load it exactly once."""
    global _model, _extractor, _labels
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        log.info("loading %s (cpu)", MODEL_ID)
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

        torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
        extractor = AutoFeatureExtractor.from_pretrained(MODEL_ID)
        model = AutoModelForAudioClassification.from_pretrained(MODEL_ID)
        model.eval()
        cfg = getattr(model, "config", None)
        id2label = dict(getattr(cfg, "id2label", {}) or {})
        _labels = [str(id2label.get(i, i)).lower() for i in range(len(id2label))]
        _extractor, _model = extractor, model
        log.info("loaded %s labels=%s", MODEL_ID, _labels)


def _decode(raw: bytes, max_seconds: float = MAX_SECONDS) -> "np.ndarray":
    """Any container/codec -> mono float32 @16k, via ffmpeg.

    ffmpeg rather than a Python decoder because the input is whatever a browser's MediaRecorder
    or a customer's phone system produced — webm/opus, m4a, amr, wav — and ffmpeg is the only
    thing that reads all of it. It is already how the main API normalises audio for STT.
    """
    with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as fh:
        fh.write(raw)
        src = fh.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", src,
             "-t", str(max_seconds), "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "f32le", "-"],
            capture_output=True, timeout=120, check=False)
        if proc.returncode != 0 or not proc.stdout:
            raise HTTPException(status_code=400,
                                detail=f"Could not decode audio: {proc.stderr.decode()[:200]}")
        return np.frombuffer(proc.stdout, dtype=np.float32)
    finally:
        try:
            os.unlink(src)
        except OSError:
            pass


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "loaded": _model is not None}


@app.post("/prosody")
async def prosody(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")

    wave = _decode(raw)
    if wave.size < SAMPLE_RATE // 2:      # under half a second carries no usable prosody
        return {"label": "unknown", "confidence": 0.0, "arousal": None, "valence": None,
                "dominance": None, "scores": {}, "model": MODEL_ID,
                "note": "audio too short for prosody"}

    _load()
    import torch

    inputs = _extractor(wave, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True)
    with torch.no_grad():
        logits = _model(**inputs).logits[0]

    if logits.ndim == 0 or logits.shape[-1] == 0:
        raise HTTPException(status_code=500, detail="Model returned no scores")

    return _result_from_probs(torch.softmax(logits, dim=-1).tolist())


def _result_from_probs(probs: list[float]) -> dict:
    """One classifier row -> the response record. Shared by both endpoints so the label
    vocabulary and the arousal/valence interpolation can never drift between them."""
    scores = {}
    for i, p in enumerate(probs):
        name = _labels[i] if i < len(_labels) else str(i)
        scores[_ALIAS.get(name, name)] = round(float(p), 4)

    label = max(scores, key=scores.get)
    conf = scores[label]
    arousal, valence = _AV.get(label, (0.5, 0.5))
    return {
        "label": label,
        "confidence": conf,
        # Interpolated toward neutral by confidence: a 40%-confident "angry" should not report
        # the same arousal as a 99%-confident one.
        "arousal": round(0.5 + (arousal - 0.5) * conf, 3),
        "valence": round(0.5 + (valence - 0.5) * conf, 3),
        "dominance": None,
        "scores": scores,
        "model": MODEL_ID,
    }


# --------------------------------------------------------------------------- #
# Per-segment prosody: one decode, many slices, batched inference
# --------------------------------------------------------------------------- #
def _parse_ranges(text: str) -> list[dict]:
    """The `segments` form field -> `[{"i", "start", "end"}]`, or a 400.

    Entries that are not a dict, have a non-integer `i`, non-numeric times or an empty range
    are DROPPED rather than rejected: the caller built them from a jsonb column some other
    code wrote, and one odd row must not cost the other three hundred their reading. Only a
    body that is not a JSON list at all is the client's fault.
    """
    try:
        items = json.loads(text or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="segments must be a JSON list")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="segments must be a JSON list")
    if len(items) > MAX_RANGES:
        raise HTTPException(status_code=400, detail=f"too many segments (max {MAX_RANGES})")
    out = []
    for item in items:
        if not isinstance(item, dict) or isinstance(item.get("i"), bool):
            continue
        try:
            i = int(item.get("i"))
            start, end = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(start) and np.isfinite(end)) or end <= start:
            continue
        out.append({"i": i, "start": start, "end": end})
    return out


def _slice_ranges(wave, ranges: list[dict], sr: int, min_seconds: float = MIN_SLICE_SECONDS):
    """Cut each `[start, end)` range out of `wave` -> `(kept, skipped)`.

    `kept` is `[(i, slice)]` for ranges at least `min_seconds` long AFTER clipping to the
    decoded audio (a range past the decode limit is clipped, not rejected); `skipped` is the
    `i` of every other range. Pure — written against `len()` and slicing only, so it runs on
    a plain list in a test without numpy or torch.
    """
    total = len(wave)
    min_samples = int(round(min_seconds * sr))
    kept, skipped = [], []
    for r in ranges:
        a = max(0, min(total, int(round(r["start"] * sr))))
        b = max(0, min(total, int(round(r["end"] * sr))))
        if b - a < min_samples:
            skipped.append(r["i"])
        else:
            kept.append((r["i"], wave[a:b]))
    return kept, skipped


def _batches(kept: list, size: int = BATCH_SIZE) -> list[list]:
    """Group `(i, slice)` pairs into batches of at most `size`, longest slices first.

    Sorting by length is what keeps padding small: a batch is padded to its longest member,
    and with the base (group-norm) wav2vec2 checkpoints that padding is zeros the model
    cannot be told to ignore (see `_classify`), so neighbours in a batch should be about the
    same length. Order within the response is restored by `i` afterwards.
    """
    ordered = sorted(kept, key=lambda pair: len(pair[1]), reverse=True)
    return [ordered[k:k + size] for k in range(0, len(ordered), size)]


def _classify(slices: list) -> list[dict]:
    """One forward pass over up to BATCH_SIZE slices, padded to the longest.

    ATTENTION MASK: `return_attention_mask` is deliberately NOT passed. The feature extractor
    then follows the checkpoint's own preprocessor config, which is the only correct choice:
    wav2vec2 models with `feat_extract_norm="group"` (wav2vec2-base, the default here) were
    trained WITHOUT a mask and expect zero padding with no mask — HF documents that passing
    one degrades them — while `feat_extract_norm="layer"` checkpoints (the -large-robust /
    xlsr family) ship `return_attention_mask=True` and need the mask so padded frames do not
    pollute the pooled logits. `**inputs` forwards `attention_mask` exactly when the extractor
    produced one. `do_normalize` (per-slice zero-mean/unit-variance) is applied before
    padding, so a quiet short slice next to a loud long one is not scaled by its neighbour.
    """
    import torch

    inputs = _extractor(slices, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True)
    with torch.no_grad():
        logits = _model(**inputs).logits
    if logits.ndim != 2 or logits.shape[0] != len(slices) or logits.shape[-1] == 0:
        raise HTTPException(status_code=500, detail="Model returned no scores")
    return [_result_from_probs(row) for row in torch.softmax(logits, dim=-1).tolist()]


def _prosody_segments_sync(raw: bytes, ranges: list[dict]) -> list[dict]:
    """Decode once, slice, classify in batches. Runs on a worker thread: ffmpeg and a CPU
    forward pass both block, and a 30-minute call must not stall /health for two minutes."""
    wave = _decode(raw, max_seconds=SEGMENTS_MAX_SECONDS)
    kept, skipped = _slice_ranges(wave, ranges, SAMPLE_RATE)
    results = {i: {"i": i, "label": "unknown", "confidence": 0.0, "arousal": None,
                   "valence": None} for i in skipped}
    if kept:
        _load()
        for batch in _batches(kept):
            for (i, _), res in zip(batch, _classify([sl for _, sl in batch])):
                results[i] = {"i": i, "label": res["label"], "confidence": res["confidence"],
                              "arousal": res["arousal"], "valence": res["valence"]}
    return [results[i] for i in sorted(results)]


@app.post("/prosody/segments")
async def prosody_segments(file: UploadFile = File(...), segments: str = Form(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")
    ranges = _parse_ranges(segments)
    if not ranges:
        return {"segments": [], "model": MODEL_ID}
    out = await asyncio.to_thread(_prosody_segments_sync, raw, ranges)
    return {"segments": out, "model": MODEL_ID}
