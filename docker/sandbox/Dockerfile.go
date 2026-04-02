FROM golang:1.22-bookworm
RUN apt-get update && apt-get install -y git make curl unzip && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 sandbox
USER sandbox
WORKDIR /workspace
