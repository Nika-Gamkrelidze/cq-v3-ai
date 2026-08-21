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

# Models are cached on a volume, so a rebuild does not re-download them.
ENV HF_HOME=/data/hf TRANSFORMERS_OFFLINE=0
EXPOSE 8080

# One worker: the model is held in-process and a second copy would double the memory for no
# throughput gain on a CPU box that is already thread-saturated per request.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
