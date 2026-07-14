.PHONY: help install-deps test-deps clean

PYTHON := python3
VENV := /opt/data/cqd_venv
VENV_PY := $(VENV)/bin/python
REQ := requirements.txt

help:
	@echo "CQD Trading Bot — Available targets:"
	@echo "  make install-deps   — create/refresh .venv and install all requirements.txt deps"
	@echo "  make test-deps      — verify all required modules are importable in .venv"
	@echo "  make clean          — remove .venv"

install-deps:
	@echo "==> Creating virtual environment if missing..."
	@uv venv $(VENV) --python $(PYTHON) 2>/dev/null || true
	@echo "==> Installing dependencies from $(REQ)..."
	uv pip install -r $(REQ) --python $(VENV_PY)
	@echo "==> Done. Activate with: source $(VENV)/bin/activate"

test-deps:
	@echo "==> Checking dependencies..."
	@$(VENV_PY) tests/test_makefile_deps.py

clean:
	@echo "==> Removing .venv..."
	rm -rf $(VENV)
	@echo "==> Done."
