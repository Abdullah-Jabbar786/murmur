.PHONY: setup test topo topo-down test-topo netem netem-clear test-netem experiments experiments-e3

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

# Run on Ubuntu with sudo. Reuse the directory to resume the same matrix.
SWEEP_DIR ?= results/m41/final
SIZE ?= 64MiB
KEEP_SEED_ONLY ?= 0
experiments:
	.venv/bin/python experiments/run.py --sweep "$(SWEEP_DIR)" --size "$(SIZE)" $(if $(filter 1,$(KEEP_SEED_ONLY)),--keep-seed-only)

E3_DIR ?= results/e3-work/final
experiments-e3:
	.venv/bin/python experiments/run.py --e3 --sweep "$(E3_DIR)" --size "$(SIZE)" --keep-seed-only
