.PHONY: lint format check

# Default target directory and files
DIR ?= .
FILES ?= .

lint: format check

format:
	@echo "Running linting for $(FILES)..."
	@(cd $(DIR) && uv run ruff format $(FILES))
	@(cd $(DIR) && uv run ruff check --fix $(FILES))

check:
	@echo "Running pyright for $(FILES)..."
	@(cd $(DIR) && uv run pyright $(FILES))

# Helper target to show available commands
help:
	@echo "Available commands:"
	@echo "  make lint [DIR=dir] [FILES=files]  - Run all linting checks"
	@echo "  make format [DIR=dir] [FILES=files] - Run ruff format and fix"
	@echo "  make check [DIR=dir] [FILES=files]  - Run pyright checks"
	@echo "  make help                          - Show this help message" 
