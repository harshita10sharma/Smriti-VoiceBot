# SMRITI VoiceBot API — deployment image.
#
# Runs a single uvicorn worker on purpose: the Indic Parler-TTS model
# (~3.7 GB) is loaded lazily into process memory on first use, and a second
# worker process would load a second full copy of it. Do not raise
# --workers above 1 unless Indic Parler-TTS is disabled for this deployment.
FROM python:3.12-slim

WORKDIR /app

# libsndfile1 is needed by soundfile (WAV encode/decode) at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
# CPU-only torch, matching the already-validated Indic Parler-TTS setup
# (no GPU on standard Render plans). Installed from the official CPU wheel
# index so the image doesn't pull CUDA dependencies it will never use.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch>=2.3 \
    && pip install --no-cache-dir transformers>=4.44 parler_tts>=0.1.0

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn smriti_voice.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
