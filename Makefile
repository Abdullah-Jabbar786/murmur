.PHONY: setup test

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

test:
	python3 -m pytest -q
