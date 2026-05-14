FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "discord.py>=2.3" fastapi uvicorn httpx

COPY deploy/discord_bot/bot.py ./bot.py

ENV DISCORD_TOKEN=""
ENV DISCORD_CHANNEL_ID=""
ENV AGENT_URL=http://agent:8000
ENV SLACK_MOCK_SECRET=dev-mock-secret
ENV BOT_PORT=8091

EXPOSE 8091

CMD ["python", "bot.py"]
