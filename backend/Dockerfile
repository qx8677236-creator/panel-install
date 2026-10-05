FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nginx \
        iproute2 \
        tar \
        curl \
        iptables \
        openssh-client \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--no-server-header"]
