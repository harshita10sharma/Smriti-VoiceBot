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
# Exact pins matching the versions actually verified this session (real
# Indic Parler-TTS synthesis succeeded against these exact numbers) --
# previously these were unquoted `>=` specifiers in a shell RUN command,
# which /bin/sh parses as output redirection (`torch>=2.3` redirects
# pip's stdout to a file named `=2.3`, silently dropping the version
# constraint entirely and littering the image with stray files). Quoting
# fixes the redirection bug; pinning exactly matches this repository's own
# reproducibility standard for every other dependency.
# torchaudio is pinned to the CPU wheel index HERE, alongside torch, even
# though nothing in this Dockerfile imports it directly: transformers/
# parler_tts pull it in transitively, and if that pull happens from the
# default PyPI index (not this CPU index) it resolves to a CUDA-linked
# build that fails at import with "OSError: libcudart.so.13: cannot open
# shared object file" -- reproduced directly on a real deployed CPU-only
# instance. Installing the exact same version here first means pip's
# later transitive resolution for transformers/parler_tts sees it as
# already satisfied and never touches it. 2.11.0 is the newest version
# published on the CPU wheel index as of this pin (torchaudio releases lag
# behind torch's; there is no 2.14.0 build there) and matches what pip
# resolves transitively for parler_tts==0.2.3 anyway -- this pin only
# fixes which index it comes from, not the version.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.14.0" "torchaudio==2.11.0" \
    && pip install --no-cache-dir "transformers==4.46.1" "parler_tts==0.2.3"

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn smriti_voice.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
