"""cq-sentiment — acoustic emotion (prosody) over HTTP, on CPU.

Deliberately the same shape as the `cq-embeddings` sidecar: one small self-hosted model behind
one endpoint, no external API key, and the audio never leaves the deployment. That last point
is the reason this is a container and not a SaaS call — the tenants are banks and clinics.

  POST /prosody   (multipart: file)  -> {label, arousal, valence, dominance, confidence, scores}
  GET  /health                       -> {status, model, loaded}

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

import io
import logging
import os
import subprocess
import tempfile
import threading

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cq-sentiment")

MODEL_ID = os.getenv("SENTIMENT_MODEL", "superb/wav2vec2-base-superb-er")
SAMPLE_RATE = 16000
MAX_SECONDS = float(os.getenv("SENTIMENT_MAX_SECONDS", "120"))

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


def _decode(raw: bytes) -> "np.ndarray":
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
             "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(SAMPLE_RATE),
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

    probs = torch.softmax(logits, dim=-1).tolist()
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
