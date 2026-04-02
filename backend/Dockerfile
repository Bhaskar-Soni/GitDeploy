FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        docker.io \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v2.24.6/docker-compose-linux-x86_64" \
       -o /usr/local/bin/docker-compose \
    && chmod +x /usr/local/bin/docker-compose

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
