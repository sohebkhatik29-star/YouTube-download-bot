FROM python:3.11-slim

WORKDIR /app

# Pre-configured Bot Credentials
ENV API_ID="30720676"
ENV API_HASH="a078e3476750afbd6db7d6c5e5e658d9"
ENV BOT_TOKEN="8780722962:AAGL9e4IVewXxLoB-tuoCzI8b7Rfdkfm6XM"
ENV ADMINS="5566977478"
ENV CHANNEL_URL="https://t.me/MoviesGroupG3"
ENV WHITELIST_ENABLED="false"
ENV PORT="8080"

# Install Node.js (Critical for YouTube JavaScript signature solver), FFmpeg, curl, git
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nodejs \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install latest yt-dlp master branch (fixes player response errors) and requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --upgrade https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .
RUN chmod +x /app/start.sh

# Persistent volume directory
VOLUME ["/app/data"]

EXPOSE 8080

CMD ["/app/start.sh"]
