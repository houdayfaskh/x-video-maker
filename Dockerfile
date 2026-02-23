FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p temp downloads

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --timeout 300 --workers 2 app:app"]
