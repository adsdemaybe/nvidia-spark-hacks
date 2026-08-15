# The validation loop, same contract as realsim/Makefile: `make loop` is the
# whole truth about this subtree's health. A green loop is the definition of
# "working". Exit code is the verdict.

.PHONY: loop lint test fixtures twin

loop: lint test
	@echo "\n=== arxr loop green ==="

lint:
	uv run ruff check packages tools tests

test:
	uv run pytest tests/ -q

# Regenerate the fixture pack every mode develops against (STRUCT_2.md 57).
fixtures:
	uv run python tools/make_fixtures.py

# Mock TwinState stream (STRUCT_2.md 58). Swap for the Isaac bridge later
# without touching any client.
twin:
	uv run python tools/mock_twin_server.py --hz 30
