# baryon-ax Slack bot. Socket Mode: outbound connections only, no port to publish.
#   docker compose -f docker-compose.bots.yml up -d --build
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
RUN useradd --create-home --uid 10001 bot && mkdir -p /data && chown bot /data
USER bot

# Proactive mode keeps which gaps it asked about here; mount a volume so restarts remember.
ENV PROACTIVE_STATE_PATH=/data/proactive_state.json
VOLUME ["/data"]

CMD ["python", "baryon_slack_bot.py"]
