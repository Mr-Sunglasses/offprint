.PHONY: help install web serve dev test lint fmt clean

help:
	@echo "install   uv sync + Playwright Chromium + npm install"
	@echo "web       build the front end into src/offprint/static"
	@echo "serve     run the app on http://127.0.0.1:8000"
	@echo "dev       API with reload + Vite dev server on :5173"
	@echo "test      pytest"
	@echo "lint      ruff"

install:
	uv sync
	uv run playwright install chromium
	cd web && npm install

web:
	cd web && npm run build

serve: web
	uv run offprint --host 0.0.0.0 --port 8000

dev:
	uv run offprint --dev --port 8000 & \
	cd web && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests

clean:
	rm -rf web/node_modules src/offprint/static .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
