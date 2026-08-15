# The validation loop for the AR/XR subsystem (Feat 4 + Feat 5).
# `make loop` is the whole truth about contract-layer + datapipe health:
# lint -> unit tests. ar-datapipe's Pinocchio/MuJoCo deps are Linux-only
# (see packages/ar-datapipe/README.md) — those tests self-skip on Windows
# via pytest.importorskip rather than failing `make loop`.

.PHONY: loop lint test fixtures mock-twin

loop: lint test
	@echo "\n=== loop green ==="

lint:
	uv run ruff check packages tools tests

test:
	uv run pytest tests/ -q

# Regenerate fixtures/ar-xr/*.jsonl and sample_episode.parquet deterministically.
fixtures:
	uv run python tools/make_fixtures.py

# Stream synthetic TwinState over WebSocket for phone/client development.
mock-twin:
	uv run python tools/mock_twin_server.py --hz 30
