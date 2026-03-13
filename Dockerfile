FROM python:3.12-slim

# 한글 폰트 설치
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

CMD gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --timeout 120