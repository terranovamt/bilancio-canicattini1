FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV HOME=/tmp \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY bilancio ./bilancio
COPY db ./db
COPY storage ./storage
COPY vision ./vision
COPY config.py logging_config.py ./
RUN mkdir -p /data /scontrini /data/logs /data/pending

CMD ["python", "-m", "bot.main"]
