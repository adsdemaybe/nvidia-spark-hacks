#!/usr/bin/env bash
# Re-fetch both corpora and rebuild the index.
#
# The corpus is a snapshot, not a live mirror, and that is the right trade: retrieval that
# silently changes under a running design loop makes two identical runs produce different
# boards for reasons nothing records. Run this deliberately, and the commit that follows
# says which docs the boards were designed against.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-../cad-generation/engine/.venv/bin/python}"

mkdir -p corpus
echo "fetching tscircuit llms.txt"
curl -sf -m 180 -L https://docs.tscircuit.com/llms.txt -o corpus/tscircuit-llms.txt

echo "fetching build123d (branch dev)"
curl -sf -m 600 -L https://codeload.github.com/gumyr/build123d/tar.gz/refs/heads/dev -o corpus/b123d.tar.gz
rm -rf corpus/b123d && tar xzf corpus/b123d.tar.gz -C corpus
mv corpus/build123d-dev corpus/b123d && rm -f corpus/b123d.tar.gz

"$PY" src/docsrag/ingest.py
"$PY" -m pytest tests/ -q
