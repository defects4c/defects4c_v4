# =============================================================================
# Makefile — defects4j Docker service
#
# Automatically sets D4J_UID/D4J_GID to match the current host user
# so you never have to edit .env for UID/GID again.
#
# Usage:
#   make up       Build & start (auto-detects UID/GID)
#   make down     Stop & remove containers
#   make logs     Tail container logs
#   make restart  Restart the service
#   make health   Check service health
#   make shell    Open a shell inside the container
#   make clean    Stop, remove containers, and prune build cache
# =============================================================================

.PHONY: up down logs restart health shell clean init

# ── Auto-detect host UID/GID and patch .env ───────────────────────
init:
	@sed -i 's/^D4J_UID=.*/D4J_UID=$(shell id -u)/' .env
	@sed -i 's/^D4J_GID=.*/D4J_GID=$(shell id -g)/' .env
	@echo "[make] Set D4J_UID=$(shell id -u) D4J_GID=$(shell id -g) in .env"

# ── Main targets ──────────────────────────────────────────────────
up: init
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

restart: init
	docker compose down
	docker compose up --build -d

health:
	@curl -sf http://localhost:$$(grep D4J_PORT .env | head -1 | cut -d= -f2)/health \
		&& echo " OK" || echo " FAIL"

shell:
	docker compose exec defects4j1 bash

clean:
	docker compose down --rmi local --volumes --remove-orphans
	docker builder prune -f

