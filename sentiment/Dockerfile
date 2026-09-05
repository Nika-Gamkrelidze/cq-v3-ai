# cq-sentiment: acoustic emotion on CPU. Same idea as cq-embeddings — one small self-hosted
# model, no external key, audio never leaves the deployment.
FROM python:3.11-slim

# ffmpeg decodes whatever the browser or phone system produced (webm/opus, m4a, amr, ...).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch from the dedicated index: the default wheel drags in ~2 GB of CUDA that can
# never be used here.
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

COPY app.py .

# The checkpoint is BAKED INTO THE IMAGE, not fetched at runtime.
#
# It used to live only on a volume with HF_HOME=/data/hf, downloaded lazily on first use.
# That makes the deployment depend on the server reaching huggingface.co at REQUEST time, and
# when that fetch is slow or blocked there is no good failure: the request simply hangs until
# the caller's read timeout, every time, and the UI reports the one thing that is not true
# after the first minute — "the voice model was still starting up". Baking it moves that
# dependency to build time, where a failure stops the build with a real error instead of
# turning into a permanently "warming" service.
ARG SENTIMENT_MODEL="superb/wav2vec2-base-superb-er"
ENV HF_HOME=/opt/hf
RUN python -c "import sys; from transformers import AutoFeatureExtractor, AutoModelForAudioClassification; m = sys.argv[1]; AutoFeatureExtractor.from_pretrained(m); AutoModelForAudioClassification.from_pretrained(m); print('baked', m)" "$SENTIMENT_MODEL"     && du -sh /opt/hf
# The image now CONTAINS one specific checkpoint, so the runtime setting must not drift from
# it — compose passes the same value as a build arg and as the env var app.py reads.
ENV SENTIMENT_MODEL=$SENTIMENT_MODEL

# No network needed for the model at runtime: if something ever does try to reach the hub,
# fail fast and loudly rather than hanging a request behind a download.
ENV TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1
EXPOSE 8080

# One worker: the model is held in-process and a second copy would double the memory for no
# throughput gain on a CPU box that is already thread-saturated per request.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
