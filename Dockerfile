FROM python:3.12-slim

# Tesseract for the printed-front OCR (FT, The Times; morning edition only)
RUN export DEBIAN_FRONTEND=noninteractive && apt-get update -qq && apt-get install -y -qq --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && printf '#!/bin/sh\nexec nice -n 15 tesseract "$@"\n' > /usr/local/bin/tesseract-nice \
    && chmod +x /usr/local/bin/tesseract-nice

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY plugin ./plugin

# OCR runs at low priority on one core, so LaraPaper's renders (and the Kindle's fetches) stay fast
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=1 TESSERACT_CMD=/usr/local/bin/tesseract-nice
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=5m --timeout=20s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=15)" || exit 1

# One worker: the edition builder runs as a background thread inside it. Never use --preload.
CMD ["gunicorn", "-w", "1", "--threads", "4", "-t", "120", "-b", "0.0.0.0:8000", "app.main:create_app()"]
