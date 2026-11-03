.PHONY: install dev run demo models test e2e lint fmt typecheck check migrate token docker-up docker-down

install:            ## install dependencies (incl. dev tools) and git hooks
	uv sync
	uv run pre-commit install

dev:                ## run with auto-reload on http://127.0.0.1:8000
	uv run uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

demo:               ## the interface with a stand-in brain; ARGS=--fake-voice needs no models either
	uv run python -m scripts.demo $(ARGS)

models:             ## download Whisper and Piper models now (otherwise on first start)
	uv run python -m scripts.download_models

e2e:                ## drive the interface in headless Chromium (needs Node + Playwright)
	node tests/e2e/voice_loop.mjs

run:                ## run without reload, listening on all interfaces
	uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy app

check: lint typecheck test   ## everything CI runs

migrate:            ## apply database migrations (also done automatically at startup)
	uv run alembic upgrade head

token:              ## print a fresh random API token for ALFRED_API_TOKEN
	@uv run python -c "import secrets; print(secrets.token_urlsafe(32))"

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
