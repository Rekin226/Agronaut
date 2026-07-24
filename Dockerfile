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

# Install Python deps first for layer caching.
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
