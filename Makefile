setup:
	uv sync --locked
	uv run pre-commit install

lint:
	uv run ruff check .

.PHONY: setup lint
