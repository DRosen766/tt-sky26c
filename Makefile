# Root Makefile.
# Simulation still lives in test/ -- see test/Makefile. Targets here are the
# ones that span the whole repo (comparison / characterization runs).

# `compare` is opt-in: it must never be picked up as the default goal, so the
# bare `make` prints help instead.
.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Targets:"
	@echo "  compare  - (not yet implemented) compare a run against its reference"
	@echo ""
	@echo "RTL/gate-level simulation lives in test/: cd test && make -B"

.PHONY: compare
compare:
	python scripts/compare_alpha.py

