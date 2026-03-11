.PHONY: help install install-dev data test test-public benchmark typecheck lint format check

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install     Install dependencies (pip install -r requirements.txt)"
	@echo "  install-dev Install dev tools (mypy, ruff, pytest)"
	@echo "  data        Convert CSV to SQLite (scripts/gaming_csv_to_db.py)"
	@echo "  test        Run all tests (excluding public/LLM tests)"
	@echo "  test-public Run public integration tests (requires OPENROUTER_API_KEY)"
	@echo "  benchmark   Run benchmark (scripts/benchmark.py --runs 3)"
	@echo "  typecheck   Run mypy on src/"
	@echo "  lint        Run ruff check"
	@echo "  format      Run ruff format"
	@echo "  check       Run typecheck + lint + test"

install:
	pip install -r requirements.txt

install-dev: install
	pip install mypy ruff pytest

data:
	python3 scripts/gaming_csv_to_db.py

test:
	python3 -m pytest tests/ --ignore=tests/test_public.py -v

test-public:
	python3 -c "from src import init_env; init_env(); import unittest; loader = unittest.TestLoader(); suite = loader.discover('tests', pattern='test_public.py'); runner = unittest.TextTestRunner(verbosity=2); runner.run(suite)"

benchmark:
	python3 scripts/benchmark.py --runs 3

typecheck:
	python3 -m mypy src

lint:
	python3 -m ruff check .

format:
	python3 -m ruff format .

check: typecheck lint test
	@echo "All checks passed."
