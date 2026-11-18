.PHONY: setup test topo topo-down test-topo

LEECHERS_PER_LAN ?= 5

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

test:
	python3 -m pytest -q

topo:
	bash infra/topology.sh --leechers-per-lan "$(LEECHERS_PER_LAN)"

topo-down:
	bash infra/teardown.sh

test-topo:
	bash infra/test_topology.sh
