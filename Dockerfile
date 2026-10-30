FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV HOME=/tmp \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ita \
    && rm -rf /var/lib/apt/lists/*
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY bilancio ./bilancio
COPY db ./db
COPY storage ./storage
COPY vision ./vision
COPY config.py logging_config.py ./
RUN mkdir -p /data /scontrini /data/logs /data/pending

# Last layers: changing the revision must not invalidate the dependency cache.
ARG APP_REVISION=""
ARG APP_COMMIT_DATE=""
ARG APP_COMMIT_SUBJECT=""
ENV APP_REVISION=${APP_REVISION} \
    APP_COMMIT_DATE=${APP_COMMIT_DATE} \
    APP_COMMIT_SUBJECT=${APP_COMMIT_SUBJECT}

CMD ["python", "-m", "bot.main"]
