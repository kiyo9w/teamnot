# TeamNoT — make targets for common workflows.
# Use `make help` to see the available targets.

PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
ifeq ($(OS),Windows_NT)
    BIN := $(VENV)/Scripts
endif
VPY    := $(BIN)/python

.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install (core only)
	@./install.sh

install-all: ## Create venv and install with every extra
	@./install.sh --all --dev

install-dev: ## Create venv and install dev tools
	@./install.sh --dev

doctor: ## Run environment check
	@$(VPY) -m teamnot.cli doctor

test: ## Run pytest
	@$(VPY) -m pytest -q

lint: ## Run ruff
	@$(VPY) -m ruff check src tests

format: ## Format with ruff
	@$(VPY) -m ruff format src tests

typecheck: ## Run mypy
	@$(VPY) -m mypy src

clean: ## Remove caches and build artifacts
	@rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +

clean-venv: ## Remove the virtualenv
	@rm -rf $(VENV)

freeze: ## Snapshot current deps
	@$(VPY) -m pip freeze > requirements.lock.txt

.PHONY: help install install-all install-dev doctor test lint format typecheck clean clean-venv freeze
