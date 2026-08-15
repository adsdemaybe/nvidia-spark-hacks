# The validation loop. `make loop` is the whole truth about the repo's health:
# lint -> unit tests -> the fixture pipeline end-to-end -> summary.
# Iterate against it; a green loop is the definition of "working".

.PHONY: loop lint test fixture run clean-run gates

loop: lint test run
	@echo "\n=== loop green ==="

lint:
	uv run ruff check packages tools tests

test:
	uv run pytest tests/ -q

fixture:
	uv run python tools/make_fixture.py

# The sim test: full pipeline on the synthetic room. Exit code is the verdict
# (0 pass, 10 degraded-but-shipping, 20+ broken).
run:
	uv run r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337

# Same but forcing every stage to re-execute (no cache).
run-cold:
	uv run r2s run-all fixtures/tiny_room --fixture -n 8 --seed 1337 \
		--force capture,reconstruct,segment,assetize,generate,shell,cousins,tasks,validate

gates:
	uv run r2s gates

clean-run:
	rm -rf .r2s
