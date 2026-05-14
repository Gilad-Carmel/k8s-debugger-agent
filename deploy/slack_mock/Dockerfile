FROM python:3.11-slim

WORKDIR /app

# Only the packages the mock service needs
RUN pip install --no-cache-dir fastapi uvicorn httpx

COPY deploy/slack_mock/app.py ./app.py

ENV AGENT_URL=http://agent:8080
ENV AGENT_CALLBACK_URL=http://agent:8080
ENV SLACK_MOCK_SECRET=dev-mock-secret

EXPOSE 8090

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
