# Agronaut — one image, two entrypoints (Streamlit web app or the Telegram bot).
# Pick with the container command: `streamlit run app.py` (default) or `python bot.py`.
FROM python:3.12-slim

# Faster, quieter Python; no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: ffmpeg is only needed if you enable voice notes (ASR); kept minimal.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch FIRST, from PyTorch's CPU wheel index. sentence-transformers pulls
# torch transitively, and the default Linux wheel drags in a multi-GB CUDA stack (cuBLAS,
# cuDNN, NCCL…) this app never uses. Pinning the CPU build keeps the image lean and the
# build fast — right for a small VPS. The later `pip install -r` then sees torch satisfied.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

# Install the rest of the Python deps (layer cached on requirement.txt).
COPY requirement.txt ./
RUN pip install -r requirement.txt

# App code.
COPY . .

# Persist the SQLite memory DB outside the image.
ENV AGRONAUT_DB=/data/agronaut.sqlite3
VOLUME ["/data"]

EXPOSE 8501

# Default: the Streamlit web app. Override in compose / `docker run` for the bot.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
