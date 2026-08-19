PY ?= python3
VENV := .venv
BIN := $(VENV)/bin
.PHONY: setup test cov lint typecheck check itest hooks clean
setup:
	@if command -v uv >/dev/null; then uv venv $(VENV) && uv pip install -e ".[dev]"; \
	else $(PY) -m venv $(VENV) && $(BIN)/pip install -U pip && $(BIN)/pip install -e ".[dev]"; fi
test:
	$(BIN)/pytest -m "not integration"
cov:
	$(BIN)/pytest -m "not integration" --cov --cov-report=term-missing --cov-fail-under=0  # raise as you build
lint:
	$(BIN)/ruff check . && $(BIN)/ruff format --check .
typecheck:
	$(BIN)/mypy
check: lint typecheck test
itest:
	$(BIN)/pytest -m integration
hooks:
	$(BIN)/pre-commit install
clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
