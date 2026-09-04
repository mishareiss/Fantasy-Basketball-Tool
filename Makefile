.DEFAULT_GOAL := help
.PHONY: help setup db-up db-down db-logs backend frontend migrate revision sync sync-ages import fixtures nba-fixtures test test-live test-nba lint fmt

# Load the local .env (gitignored) and pass it to every recipe, so the frontend gets
# NEXT_PUBLIC_API_BASE_URL and docker compose gets the POSTGRES_* values.
ifneq (,$(wildcard .env))
include .env
export
endif

UV := uv --project backend

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

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

sync-ages: ## Match nba.com's roster to our players and fill in birthdates + ages (idempotent)
	cd backend && uv run python -m scripts.sync_ages

# Dry run by default (prints what it *would* write); add COMMIT=1 to store it.
#   make import KIND=adp SOURCE=hashtag SEASON=2027 FILE=~/Downloads/adp.csv
#   make import KIND=adp SOURCE=hashtag FILE=adp.csv COMMIT=1 ARGS="--strict"
#   make import KIND=projection SOURCE=hashtag SEASON=2027 FILE=proj.csv BASIS=per_game
#   make import KIND=ranking SOURCE=hashtag SEASON=2027 NAME="Dynasty Top 200" \
#     HORIZON=dynasty FILE=top200.csv
# BASIS is projection-only: per_game (the usual export: averages plus a GP column) or season.
# NAME is ranking-only: the set's label; defaults to SOURCE. HORIZON is ranking-only and
# REQUIRED: dynasty or redraft, since a rank-only list has no stats to age-adjust.
# (source, NAME, season, HORIZON) identifies the stored set, and re-importing it REPLACES that
# set's entries wholesale.
import: ## Import a CSV/paste (KIND= SOURCE= [SEASON=] FILE= [BASIS=] [NAME=] [HORIZON=] [COMMIT=1] [ARGS=...])
	cd backend && uv run python -m scripts.import_data \
		--kind "$(KIND)" --source "$(SOURCE)" \
		$(if $(SEASON),--season "$(SEASON)",) $(if $(FILE),--file "$(FILE)",) \
		$(if $(BASIS),--basis "$(BASIS)",) $(if $(NAME),--name "$(NAME)",) \
		$(if $(HORIZON),--horizon "$(HORIZON)",) \
		$(if $(COMMIT),--commit,) $(ARGS)

fixtures: ## Re-record the sanitized ESPN test fixtures from a live pull
	cd backend && uv run python -m scripts.record_fixtures

nba-fixtures: ## Re-record the offline nba.com fixtures (roster + birthdates)
	cd backend && uv run python -m scripts.record_nba_fixtures

backend: ## Run the API with reload on :8000
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend: ## Run the Next.js dev server on :3000
	cd frontend && npm run dev

test: ## Run the backend test suite (offline: fixtures only, no ESPN cookies needed)
	cd backend && uv run pytest

test-live: ## Run only the tests that hit the real ESPN API (needs cookies in .env)
	cd backend && uv run pytest -m live

test-nba: ## Run only the tests that hit the real nba.com stats API (no credentials needed)
	cd backend && uv run pytest -m nbaapi

lint: ## Lint and format-check the backend
	$(UV) run ruff check backend
	$(UV) run ruff format --check backend

fmt: ## Auto-fix lint issues and format the backend
	$(UV) run ruff check --fix backend
	$(UV) run ruff format backend
