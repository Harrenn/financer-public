PYTHON ?= python3
VENV ?= backend/.venv
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip

.PHONY: setup run run-api test

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PIP) install -r requirements.txt

run:
	$(PYTHON) app.py

run-api:
	$(PYTHON) app.py --api

test:
	$(VENV_PYTHON) -m pytest backend/tests_v2
