FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Moscow

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

RUN mkdir -p /app/data

CMD ["python", "-m", "src.main"]
