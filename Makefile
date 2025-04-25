.PHONY: lint format check lint-check

# Default target directory and files
DIR ?= .
FILES ?= .
# Default to fix mode (backwards compatible)
CHECK_ONLY ?= false

lint: format check

test:
	@echo "Running tests for $(FILES)..."
	@(cd $(DIR) && uv run pytest -vvv $(FILES) $(filter-out $@,$(MAKECMDGOALS)))

format:
	@echo "Running linting for $(FILES)..."
ifeq ($(CHECK_ONLY),true)
	@echo "Running in check-only mode..."
	@(cd $(DIR) && uv run ruff format --check $(FILES))
	@(cd $(DIR) && uv run ruff check $(FILES))
else
	@(cd $(DIR) && uv run ruff format $(FILES))
	@(cd $(DIR) && uv run ruff check --fix $(FILES))
endif

check:
	@echo "Running pyright for $(FILES)..."
	@(cd $(DIR) && uv run pyright $(FILES))

# Helper target to show available commands
help:
	@echo "Available commands:"
	@echo "  make lint [DIR=dir] [FILES=files] [CHECK_ONLY=true|false] - Run all linting checks"
	@echo "  make format [DIR=dir] [FILES=files] [CHECK_ONLY=true|false] - Run ruff format and fix"
	@echo "  make check [DIR=dir] [FILES=files]  - Run pyright checks"
	@echo "  make help                          - Show this help message"

%:
	@: 