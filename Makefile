.PHONY: setup test topo topo-down test-topo netem netem-clear test-netem experiments experiments-e1-e2 experiments-e3 experiments-e4 experiments-e5

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

# All four runners share one namespace lab. Separate recipe lines run in order,
# including under make -j, and stop the sequence if a runner fails.
REPRO_DIR ?= results/reproduce-work
experiments: SWEEP_DIR = $(REPRO_DIR)/e1-e2
experiments: E3_DIR = $(REPRO_DIR)/e3
experiments: E4_DIR = $(REPRO_DIR)/e4
experiments: E5_DIR = $(REPRO_DIR)/e5
experiments: KEEP_SEED_ONLY = 1
experiments:
	$(MAKE) experiments-e1-e2 SWEEP_DIR="$(SWEEP_DIR)" KEEP_SEED_ONLY="$(KEEP_SEED_ONLY)"
	$(MAKE) experiments-e3 E3_DIR="$(E3_DIR)"
	$(MAKE) experiments-e4 E4_DIR="$(E4_DIR)"
	$(MAKE) experiments-e5 E5_DIR="$(E5_DIR)"

# Standalone targets retain their original output paths and settings.
SWEEP_DIR ?= results/m41/final
SIZE ?= 64MiB
KEEP_SEED_ONLY ?= 0
experiments-e1-e2:
	.venv/bin/python experiments/run.py --sweep "$(SWEEP_DIR)" --size "$(SIZE)" $(if $(filter 1,$(KEEP_SEED_ONLY)),--keep-seed-only)

E3_DIR ?= results/e3-work/final
experiments-e3:
	.venv/bin/python experiments/run.py --e3 --sweep "$(E3_DIR)" --size "$(SIZE)" --keep-seed-only

E4_DIR ?= results/e4-work/final
experiments-e4:
	.venv/bin/python experiments/run.py --e4 --sweep "$(E4_DIR)" --size "$(SIZE)"

E5_DIR ?= results/e5-work/final
experiments-e5:
	.venv/bin/python experiments/run.py --e5 --sweep "$(E5_DIR)" --size "$(SIZE)"
