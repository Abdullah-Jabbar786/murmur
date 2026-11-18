.PHONY: setup test topo topo-down test-topo netem netem-clear test-netem

LEECHERS_PER_LAN ?= 5
export ARGS

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

netem:
	set -f; bash infra/netem.sh apply $$ARGS

netem-clear:
	bash infra/netem.sh clear

test-netem:
	python3 infra/test_netem.py
