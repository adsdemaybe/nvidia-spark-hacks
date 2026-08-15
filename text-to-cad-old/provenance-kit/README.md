# provenance-kit

Tamper-evident signing and public timestamping for files and directory trees.

Signs content with Ed25519 so any modification is instantly detectable by
anyone holding your public key, and anchors the signed manifest in the
OpenTimestamps public log so nobody can out-date your claim of authorship.

## What it proves — and what it doesn't

| Claim | Proven by | Status |
| --- | --- | --- |
| "This copy is unmodified since signing" | Ed25519 signature over canonical manifest | anyone can verify offline |
| "The signer holds this key" | signature | yes, but key ↔ identity binding is out of band |
| "This content existed before time T" | OpenTimestamps receipt (Bitcoin-anchored) | verifiable after ~hours of aggregation |
| "Nobody can copy my work" | — | **not provable by anything.** This kit produces detection and evidence, not prevention. |

The strip-and-resign attack (forker deletes your manifest, signs with their
key) defeats signatures alone. It does not defeat the anchor: their earliest
possible timestamp is later than yours. Sign **and** anchor, always.

## Setup

```bash
cd provenance-kit
python3 -m venv .venv
.venv/bin/pip install cryptography opentimestamps-client
```

## Usage

```bash
alias pk="/path/to/provenance-kit/.venv/bin/python -m pk.cli"
export PATH="/path/to/provenance-kit/.venv/bin:$PATH"   # so `ots` is found

pk keygen                       # once; private key lands in ~/.provenance-kit (0600)
pk sign  myproject/ --author "you@example.com" --comment "v1.0"
pk anchor myproject/            # publishes manifest hash to OpenTimestamps
pk verify myproject/ --pubkey ~/.provenance-kit/pk_ed25519.pub
pk anchor --check myproject/    # verify the timestamp receipt
```

- Directory targets get `myproject/.pk/manifest.json` (+ `.ots` receipt). Commit both.
- File targets get `file.pk.json` beside the file.
- `.git`, `.venv`, `__pycache__`, `node_modules`, `.pk` are excluded from hashing.
- Verifying without `--pubkey` checks integrity only; identity requires the
  author's key obtained out of band (their website, DNS, keybase, a signed
  git tag — anywhere the forker doesn't control).

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Covers: clean verify, per-file tamper localization (modified/added/deleted/
renamed), payload forgery, strip-and-resign with and without key pinning,
excluded-dir noise.

## Threat model, honestly

- **Beats:** silent modification of a signed copy; forged authorship metadata;
  backdated authorship claims (once anchored); "I made this first" disputes.
- **Does not beat:** wholesale copying with manifest stripped *if you never
  anchored*; a forker re-publishing under their own sig (you win on timestamp,
  not on prevention); anyone rewriting the work from scratch.
- Private key compromise = game over for future signatures; anchored past
  receipts stay valid.
- The `signed_at` field is self-reported. Only the anchor makes time claims
  trustworthy.
