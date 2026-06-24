# =============================================================================
# Makefile — defects4c Docker service (port 8095)
#
# Auto-detects host UID/GID into .env so volume-mounted writes stay
# owned by the host user. The container internally listens on 11111;
# docker-compose maps host port 8095 → container 11111.
#
# Usage:
#   make up       Build & start (auto-detects UID/GID)
#   make down     Stop & remove containers
#   make logs     Tail container logs
#   make restart  Restart the service (after editing defectsc_tpl/)
#   make health   Check service health
#   make shell    Open a shell inside the container
#   make clean    Stop, remove containers, and prune build cache
# =============================================================================

.PHONY: up down logs restart health shell clean init

# ── Auto-detect host UID/GID and patch .env ───────────────────────
init:
	@touch .env
	@grep -q '^D4C_UID=' .env || echo "D4C_UID=$(shell id -u)"   >> .env
	@grep -q '^D4C_GID=' .env || echo "D4C_GID=$(shell id -g)"   >> .env
	@grep -q '^D4C_PORT=' .env || echo "D4C_PORT=8095"           >> .env
	@sed -i 's/^D4C_UID=.*/D4C_UID=$(shell id -u)/' .env
	@sed -i 's/^D4C_GID=.*/D4C_GID=$(shell id -g)/' .env
	@echo "[make] D4C_UID=$(shell id -u) D4C_GID=$(shell id -g) D4C_PORT=$$(grep D4C_PORT .env | head -1 | cut -d= -f2) (in .env)"

# ── Main targets ──────────────────────────────────────────────────
up: init
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

restart: init
	docker compose restart

health:
	@PORT=$$(grep -h '^D4C_PORT=' .env 2>/dev/null | head -1 | cut -d= -f2); \
	 PORT=$${PORT:-8095}; \
	 curl -sf http://127.0.0.1:$$PORT/health && echo " OK" || echo " FAIL"

shell:
	docker compose exec defects4c bash

clean:
	docker compose down --rmi local --volumes --remove-orphans
	docker builder prune -f
