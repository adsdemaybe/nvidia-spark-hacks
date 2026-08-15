"""`python -m engine.sourcing` — the catalogue-build tool.

Three subcommands, matching the three things §6 says happen at catalogue-build
time and nowhere else:

    debt      what in the catalogue has not been through the sourcing pipeline
    search    ask the configured distributors for parts matching a request
    cache     what has been fetched, and what was quarantined

`debt` is the one that runs on a machine with no keys and no network, which is
most of them. It answers "which numbers are we still trusting because somebody
typed them in", and it is the work queue §6.1's librarian and its human reviewer
work down.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from engine.catalogue import CATALOGUES, unsourced_entries
from engine.sourcing.cache import SourcingCache
from engine.sourcing.providers import PartQuery, available_providers, online, search


def _debt(args) -> int:
    entries = unsourced_entries()
    if args.catalogue:
        entries = [e for e in entries if e[0] == args.catalogue]

    by_status = Counter(status for *_, status in entries)
    total_values = sum(
        1
        for cat in CATALOGUES.values()
        for key in cat.keys()
        for _ in cat[key].model_fields
    )

    print(f"{len(entries)} values are not CONFIRMED, across {len(CATALOGUES)} catalogues")
    for status, count in sorted(by_status.items()):
        print(f"  {status:10s} {count}")
    print()
    print("§12 non-negotiable #9: a number enters the catalogue only through the")
    print("sourcing pipeline with a citation. These predate that pipeline; each one")
    print("is a librarian extraction plus a human confirmation away from CONFIRMED.")
    print()

    if args.verbose:
        current = ""
        for catalogue, key, field, status in entries:
            if f"{catalogue}/{key}" != current:
                current = f"{catalogue}/{key}"
                print(f"  {current}")
            print(f"    [{status:8s}] {field}")
    else:
        worst = [e for e in entries if e[3] == "ASSUMED"]
        print(f"The {len(worst)} ASSUMED values are the ones to source first:")
        for catalogue, key, field, _ in worst[: args.limit]:
            print(f"  {catalogue}/{key}.{field}")
        if len(worst) > args.limit:
            print(f"  ... and {len(worst) - args.limit} more (--verbose for all)")

    # Not an error: this is a report, and exiting non-zero would make it
    # unusable in a pre-commit hook, which is where it belongs.
    return 0


def _search(args) -> int:
    if not online():
        print("SOURCING_ONLINE is not set — this will serve from cache only.", file=sys.stderr)
    configured = [p.name for p in available_providers()]
    print(f"configured providers: {configured or 'none'}", file=sys.stderr)

    offers = search(PartQuery(text=args.query, limit=args.limit))
    if not offers:
        print("no offers. With no keys and nothing cached, that is the expected result.")
        return 1

    print(f"{'provider':10s} {'mpn':24s} {'manufacturer':22s} {'stock':>8s}  datasheet")
    for offer in offers:
        print(
            f"{offer.provider:10s} {offer.mpn:24.24s} {offer.manufacturer:22.22s} "
            f"{offer.stock if offer.stock is not None else '-':>8}  {offer.datasheet_url[:60]}"
        )
    print()
    print("Nothing above has entered the catalogue. §6.3: an agent may propose a part;")
    print("the librarian extracts its numbers with citations, and a human confirms them.")
    return 0


def _cache(args) -> int:
    store = SourcingCache()
    entries = store.entries()
    print(f"{len(entries)} cached fetches under {store.root}")
    quarantined = store.quarantined()
    if quarantined:
        print(f"\n{len(quarantined)} quarantined — kept, not deleted, because the evidence matters:")
        for entry in quarantined:
            print(f"  {entry.key}\n      {entry.quarantine_reason}")
    for entry in entries[: args.limit]:
        flag = "QUARANTINED " if entry.quarantined else ""
        print(f"  {flag}{entry.sha256[:12]}  {entry.fetched_at or '-':20s}  {entry.key[:70]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.sourcing")
    subs = parser.add_subparsers(dest="command", required=True)

    debt = subs.add_parser("debt", help="catalogue values that have not been sourced")
    debt.add_argument("--catalogue", default="", help="restrict to one catalogue")
    debt.add_argument("--limit", type=int, default=20)
    debt.add_argument("--verbose", action="store_true")
    debt.set_defaults(func=_debt)

    find = subs.add_parser("search", help="search the configured distributors")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=10)
    find.set_defaults(func=_search)

    cache = subs.add_parser("cache", help="what has been fetched")
    cache.add_argument("--limit", type=int, default=30)
    cache.set_defaults(func=_cache)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
