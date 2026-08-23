.DEFAULT_GOAL := help
.PHONY: help setup db-up db-down db-logs backend frontend migrate revision sync fixtures test test-live lint fmt

# Load the local .env (gitignored) and pass it to every recipe, so the frontend gets
# NEXT_PUBLIC_API_BASE_URL and docker compose gets the POSTGRES_* values.
ifneq (,$(wildcard .env))
include .env
export
endif

UV := uv --project backend

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	$(UV) sync
	cd frontend && npm install

db-up: ## Start the local Postgres 16 container
	docker compose up -d postgres

db-down: ## Stop the Postgres container (data survives in the named volume)
	docker compose down

db-logs: ## Tail Postgres logs
	docker compose logs -f postgres

migrate: ## Apply all migrations (alembic upgrade head)
	cd backend && uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add players"
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

sync: ## Pull scoring settings + player pool from ESPN into the DB (idempotent)
	cd backend && uv run python -m scripts.sync_league

fixtures: ## Re-record the sanitized ESPN test fixtures from a live pull
	cd backend && uv run python -m scripts.record_fixtures

backend: ## Run the API with reload on :8000
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend: ## Run the Next.js dev server on :3000
	cd frontend && npm run dev

test: ## Run the backend test suite (offline: fixtures only, no ESPN cookies needed)
	cd backend && uv run pytest

test-live: ## Run only the tests that hit the real ESPN API (needs cookies in .env)
	cd backend && uv run pytest -m live

lint: ## Lint and format-check the backend
	$(UV) run ruff check backend
	$(UV) run ruff format --check backend

fmt: ## Auto-fix lint issues and format the backend
	$(UV) run ruff check --fix backend
	$(UV) run ruff format backend
