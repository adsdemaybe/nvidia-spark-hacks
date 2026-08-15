"""Command-line interface.

    pk keygen                     generate a keypair in ~/.provenance-kit
    pk sign <target>              sign a file or directory tree
    pk verify <target>            verify content + signature
    pk anchor <target>            timestamp the manifest via OpenTimestamps
    pk anchor --check <target>    verify an existing timestamp receipt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import anchor as anchor_mod
from . import keys
from .manifest import (
    create_manifest,
    manifest_path_for,
    read_manifest,
    verify_manifest,
    write_manifest,
)


def cmd_keygen(args: argparse.Namespace) -> int:
    try:
        priv, pub = keys.generate_keypair()
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"private key: {priv}  (keep this offline; never commit it)")
    print(f"public key:  {pub}")
    print(f"share this:  {pub.read_text().strip()}")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"error: no such target: {target}", file=sys.stderr)
        return 1
    try:
        private_key = keys.load_private_key()
    except (FileNotFoundError, TypeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    manifest = create_manifest(
        target, private_key, author=args.author, comment=args.comment
    )
    out_path = manifest_path_for(target)
    write_manifest(manifest, out_path)
    payload = manifest["payload"]
    print(f"signed {payload['target_kind']}: {target}")
    print(f"root hash: {payload['root_hash']}")
    print(f"files covered: {len(payload['files'])}")
    print(f"manifest: {out_path}")
    print("note: not yet anchored — run `pk anchor` to prove priority")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    m_path = manifest_path_for(target)
    if not m_path.exists():
        print(f"error: no manifest at {m_path} — was this ever signed?", file=sys.stderr)
        return 1
    expected_key = None
    if args.pubkey:
        expected_key = Path(args.pubkey).read_text().strip() if Path(args.pubkey).exists() else args.pubkey
    else:
        print(
            "warning: verifying against the key embedded in the manifest. "
            "That proves integrity, not identity — pass --pubkey with a key "
            "you obtained from the author directly for the full check."
        )
    problems = verify_manifest(read_manifest(m_path), target, expected_key)
    if problems:
        print("TAMPER EVIDENT — verification FAILED:")
        for p in problems:
            print(f"  {p}")
        return 1
    payload = read_manifest(m_path)["payload"]
    print("OK — content matches signed manifest")
    print(f"  signed at: {payload['signed_at']} (claimed; trust only if anchored)")
    print(f"  signer:    {payload['signer_public_key']}")
    if payload.get("author"):
        print(f"  author:    {payload['author']} (claimed)")
    return 0


def cmd_anchor(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    m_path = manifest_path_for(target)
    if not m_path.exists():
        print(f"error: no manifest at {m_path} — sign first", file=sys.stderr)
        return 1
    if args.check:
        ok, message = anchor_mod.verify_anchor(m_path)
    else:
        ok, message = anchor_mod.anchor_manifest(m_path)
    print(message)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pk", description="tamper-evident signing for files and trees"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("keygen", help="generate an Ed25519 keypair")

    p_sign = sub.add_parser("sign", help="sign a file or directory")
    p_sign.add_argument("target")
    p_sign.add_argument("--author", help="claimed author name/email")
    p_sign.add_argument("--comment", help="free-form note stored in the manifest")

    p_verify = sub.add_parser("verify", help="verify a signed file or directory")
    p_verify.add_argument("target")
    p_verify.add_argument(
        "--pubkey", help="expected public key (hex string or path to .pub file)"
    )

    p_anchor = sub.add_parser("anchor", help="timestamp the manifest publicly")
    p_anchor.add_argument("target")
    p_anchor.add_argument(
        "--check", action="store_true", help="verify existing receipt instead"
    )

    args = parser.parse_args(argv)
    return {
        "keygen": cmd_keygen,
        "sign": cmd_sign,
        "verify": cmd_verify,
        "anchor": cmd_anchor,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
