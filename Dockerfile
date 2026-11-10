# Для переезда на VPS: docker build -t demo-bot . && docker run -d --restart=always --env-file .env demo-bot
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY kb ./kb
CMD ["python", "-m", "bot"]
