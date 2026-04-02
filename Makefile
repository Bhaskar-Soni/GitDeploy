.PHONY: dev build-sandbox-images migrate seed pull-model genkey test cleanup-orphans clean

dev:
	docker compose up --build

build-sandbox-images:
	docker build -f docker/sandbox/Dockerfile.node    -t gitdeploy-node:latest .
	docker build -f docker/sandbox/Dockerfile.python  -t gitdeploy-python:latest .
	docker build -f docker/sandbox/Dockerfile.go      -t gitdeploy-go:latest .
	docker build -f docker/sandbox/Dockerfile.rust    -t gitdeploy-rust:latest .
	docker build -f docker/sandbox/Dockerfile.generic -t gitdeploy-generic:latest .

migrate:
	docker compose run --rm api alembic upgrade head

seed:
	docker compose run --rm api python -m db.seed

pull-model:
	@echo "No local model needed — configure AI_PROVIDER and AI_API_KEY in .env"

genkey:
	@python3 -c "from cryptography.fernet import Fernet; print('SECRET_KEY=' + Fernet.generate_key().decode())"

test:
	docker compose run --rm api pytest tests/ -v

cleanup-orphans:
	@docker ps -a --filter "name=gitdeploy_db_" --format "{{.Names}}" | xargs -r docker rm -f
	@docker network ls --filter "name=gitdeploy_net_" --format "{{.Name}}" | xargs -r docker network rm

clean:
	docker compose down -v
	rm -rf /tmp/gitdeploy
	$(MAKE) cleanup-orphans
