"""Where parametric numbers come from (§6.1).

| Source | Gives | Serves |
|---|---|---|
| Nexar (Octopart) | cross-distributor search, specs, datasheet links, lifecycle | catalogue seeding, alternates |
| Digi-Key / Mouser | parametric attributes, price breaks, live stock, CAD links | catalogue + BOM pricing |
| LCSC / JLC parts | stock + assembly eligibility (basic/extended) | shared with `pcb-ai`'s BOM |

Three things about the shape of this module are deliberate.

**Offline is the default, not the fallback.** `SOURCING_ONLINE=1` opts in. A
module that reaches the network unless told not to will eventually be imported
by something on the evaluation path, and then a coverage sweep depends on
Digi-Key's uptime. Making the network the exception means that mistake fails
closed.

**Missing keys degrade to cache-only with a warning, and the warning is
mandatory.** §6.2: "API keys are config, absence degrades to cache-only with a
loud warning — local-first holds." Silence here would make a stale cache
indistinguishable from a live fetch, which is precisely the failure that lets a
discontinued part sit in a BOM for a year.

**No provider returns a number that lands in the catalogue directly.** A
`PartOffer` is what a distributor says it has; turning that into a catalogue
entry goes through the librarian and a human confirmation (§6.3). The type
system says so: `PartOffer` holds strings and prices, and there is no method on
it that produces a `Quantity`.

Only `urllib` is used, so this adds no dependency to a package whose whole point
is that it runs on one machine with no services.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass, field
from typing import Protocol

from engine.sourcing.cache import CacheEntry, SourcingCache

OFFLINE_ENV = "SOURCING_ONLINE"
_TIMEOUT_S = 20.0


class ProviderUnavailable(RuntimeError):
    """No credentials, or the network is off. Never raised past `search()`."""


def online() -> bool:
    """Whether the sourcing layer may touch the network at all."""
    return os.environ.get(OFFLINE_ENV, "").strip() not in ("", "0", "false", "no")


@dataclass(frozen=True)
class PartQuery:
    """What an agent is allowed to ask for: a class and constraints, not a part.

    §6.3: an agent may *propose* a part or *request* a search ("NEMA17 class,
    >=0.4 N*m at speed, <=350 g"). It expresses requirements; the harness
    resolves them. `text` carries the free-form class, `constraints` the
    checkable part — kept separate so the constraints can be verified against
    what comes back rather than trusted to have been honoured.
    """

    text: str
    constraints: dict[str, str] = field(default_factory=dict)
    limit: int = 10

    def cache_key(self, provider: str) -> str:
        payload = json.dumps(
            {"text": self.text, "constraints": self.constraints, "limit": self.limit},
            sort_keys=True,
        )
        return f"{provider}:search:{payload}"


@dataclass(frozen=True)
class PartOffer:
    """One distributor's answer. Strings and prices — deliberately no Quantity.

    A price and a stock level are facts about a listing. A torque is a fact about
    a part, and it does not enter the catalogue from here: `datasheet_url` is the
    handoff to the librarian (§6.1), which extracts with citations and lands the
    value as INFERRED pending a human check.
    """

    provider: str
    mpn: str
    manufacturer: str
    description: str = ""
    datasheet_url: str = ""
    cad_url: str = ""
    stock: int | None = None
    unit_price_usd: float | None = None
    distributor_sku: str = ""
    lifecycle: str = ""
    # LCSC only, and the reason LCSC is not interchangeable with the others:
    # this decides whether JLC will place the part, which changes the cost of a
    # board and is invisible in every other distributor's data (§7.4).
    jlc_assembly: str = ""
    attributes: dict[str, str] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def configured(self) -> bool:
        """True when this provider has the credentials it needs."""

    def search(self, query: PartQuery) -> list[PartOffer]:
        """Live search. Only called when `online()` and `configured()`."""


def _http_json(url: str, *, headers: dict[str, str] | None = None, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderUnavailable(f"{url} returned HTTP {exc.code}: {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise ProviderUnavailable(f"{url} unreachable: {exc.reason}") from None


class NexarProvider:
    """Nexar (Octopart) — cross-distributor search and datasheet links.

    The one to start a search with, because it answers "who sells this and what
    is it" across distributors in one call, which is what "find me a NEMA17 with
    at least 0.4 N*m" actually needs.
    """

    name = "nexar"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("NEXAR_TOKEN", "")

    def configured(self) -> bool:
        return bool(self.token)

    def search(self, query: PartQuery) -> list[PartOffer]:
        gql = {
            "query": """
              query Search($q: String!, $limit: Int!) {
                supSearchMpn(q: $q, limit: $limit) {
                  results {
                    part {
                      mpn
                      manufacturer { name }
                      shortDescription
                      bestDatasheet { url }
                      medianPrice1000 { price }
                      sellers { company { name } offers { inventoryLevel sku } }
                    }
                  }
                }
              }
            """,
            "variables": {"q": query.text, "limit": query.limit},
        }
        payload = _http_json(
            "https://api.nexar.com/graphql",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(gql).encode("utf-8"),
        )
        results = (
            payload.get("data", {}).get("supSearchMpn", {}).get("results") or []
        )
        offers: list[PartOffer] = []
        for row in results:
            part = row.get("part") or {}
            sellers = part.get("sellers") or []
            stock = None
            sku = ""
            for seller in sellers:
                for offer in seller.get("offers") or []:
                    level = offer.get("inventoryLevel")
                    if level is not None and (stock is None or level > stock):
                        stock, sku = level, offer.get("sku", "")
            offers.append(
                PartOffer(
                    provider=self.name,
                    mpn=part.get("mpn", ""),
                    manufacturer=(part.get("manufacturer") or {}).get("name", ""),
                    description=part.get("shortDescription", ""),
                    datasheet_url=(part.get("bestDatasheet") or {}).get("url", ""),
                    stock=stock,
                    unit_price_usd=(part.get("medianPrice1000") or {}).get("price"),
                    distributor_sku=sku,
                )
            )
        return offers


class DigiKeyProvider:
    """Digi-Key — parametric attributes, price breaks, live stock, CAD links."""

    name = "digikey"

    def __init__(self, token: str | None = None, client_id: str | None = None):
        self.token = token or os.environ.get("DIGIKEY_ACCESS_TOKEN", "")
        self.client_id = client_id or os.environ.get("DIGIKEY_CLIENT_ID", "")

    def configured(self) -> bool:
        return bool(self.token and self.client_id)

    def search(self, query: PartQuery) -> list[PartOffer]:
        payload = _http_json(
            "https://api.digikey.com/products/v4/search/keyword",
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-DIGIKEY-Client-Id": self.client_id,
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {"Keywords": query.text, "Limit": query.limit, "Offset": 0}
            ).encode("utf-8"),
        )
        offers: list[PartOffer] = []
        for product in payload.get("Products") or []:
            offers.append(
                PartOffer(
                    provider=self.name,
                    mpn=product.get("ManufacturerProductNumber", ""),
                    manufacturer=(product.get("Manufacturer") or {}).get("Name", ""),
                    description=product.get("Description", {}).get("ProductDescription", ""),
                    datasheet_url=product.get("DatasheetUrl", ""),
                    stock=product.get("QuantityAvailable"),
                    unit_price_usd=product.get("UnitPrice"),
                    distributor_sku=(product.get("ProductVariations") or [{}])[0].get(
                        "DigiKeyProductNumber", ""
                    ),
                    lifecycle=(product.get("ProductStatus") or {}).get("Status", ""),
                    attributes={
                        p.get("ParameterText", ""): p.get("ValueText", "")
                        for p in product.get("Parameters") or []
                    },
                )
            )
        return offers


class MouserProvider:
    """Mouser — the second parametric source, for cross-checking Digi-Key."""

    name = "mouser"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("MOUSER_API_KEY", "")

    def configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: PartQuery) -> list[PartOffer]:
        payload = _http_json(
            f"https://api.mouser.com/api/v1/search/keyword?apiKey={urllib.parse.quote(self.api_key)}",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "SearchByKeywordRequest": {
                        "keyword": query.text,
                        "records": query.limit,
                        "startingRecord": 0,
                    }
                }
            ).encode("utf-8"),
        )
        parts = (payload.get("SearchResults") or {}).get("Parts") or []
        offers: list[PartOffer] = []
        for part in parts:
            price = None
            breaks = part.get("PriceBreaks") or []
            if breaks:
                raw = str(breaks[0].get("Price", "")).lstrip("$").replace(",", "")
                try:
                    price = float(raw)
                except ValueError:
                    price = None
            stock = None
            try:
                stock = int(str(part.get("Availability", "")).split()[0])
            except (ValueError, IndexError):
                stock = None
            offers.append(
                PartOffer(
                    provider=self.name,
                    mpn=part.get("ManufacturerPartNumber", ""),
                    manufacturer=part.get("Manufacturer", ""),
                    description=part.get("Description", ""),
                    datasheet_url=part.get("DataSheetUrl", ""),
                    stock=stock,
                    unit_price_usd=price,
                    distributor_sku=part.get("MouserPartNumber", ""),
                    lifecycle=part.get("LifecycleStatus", ""),
                )
            )
        return offers


class LCSCProvider:
    """LCSC — stock and, uniquely, JLC assembly eligibility.

    §6.1: "a part the fab can't place is flagged at selection, not at order
    time". Everything else here is interchangeable with another distributor;
    this is not.
    """

    name = "lcsc"

    def __init__(self, endpoint: str | None = None):
        # No public documented API with a stable contract, so the endpoint is
        # configuration rather than a constant. Absent, this provider is simply
        # not configured, which is the honest state — better than an endpoint
        # baked in here that silently changes shape.
        self.endpoint = endpoint or os.environ.get("LCSC_API_URL", "")

    def configured(self) -> bool:
        return bool(self.endpoint)

    def search(self, query: PartQuery) -> list[PartOffer]:
        payload = _http_json(
            f"{self.endpoint.rstrip('/')}/search?"
            + urllib.parse.urlencode({"q": query.text, "limit": query.limit})
        )
        offers: list[PartOffer] = []
        for row in payload.get("results") or []:
            offers.append(
                PartOffer(
                    provider=self.name,
                    mpn=row.get("mpn", ""),
                    manufacturer=row.get("manufacturer", ""),
                    description=row.get("description", ""),
                    datasheet_url=row.get("datasheet", ""),
                    stock=row.get("stock"),
                    unit_price_usd=row.get("price"),
                    distributor_sku=row.get("lcsc", ""),
                    jlc_assembly=row.get("assembly", ""),
                )
            )
        return offers


def all_providers() -> list[Provider]:
    return [NexarProvider(), DigiKeyProvider(), MouserProvider(), LCSCProvider()]


def available_providers() -> list[Provider]:
    return [p for p in all_providers() if p.configured()]


def search(
    query: PartQuery, *, cache: SourcingCache | None = None, now: str = ""
) -> list[PartOffer]:
    """Search every configured provider, caching every response by content hash.

    Never raises for want of credentials or network. It returns what the cache
    has and warns — once per provider per call — about what it could not reach.
    A build that quietly returns fewer parts is a build that quietly picks a
    worse motor, so the warning is the load-bearing part of this function.
    """
    cache = cache or SourcingCache()
    providers = all_providers()
    offers: list[PartOffer] = []

    for provider in providers:
        key = query.cache_key(provider.name)
        can_fetch = online() and provider.configured()

        if not can_fetch:
            cached = cache.lookup(key)
            reason = (
                "the network is disabled (set SOURCING_ONLINE=1 to enable)"
                if not online()
                else f"{provider.name} has no credentials configured"
            )
            if cached and not cached.quarantined:
                warnings.warn(
                    f"sourcing: serving {provider.name} results from cache because "
                    f"{reason}. These were fetched at {cached.fetched_at or 'an unrecorded time'} "
                    "and may be stale — stock, price and lifecycle especially.",
                    stacklevel=2,
                )
                offers.extend(
                    PartOffer(**row) for row in json.loads(cache.get(cached.sha256))
                )
            else:
                warnings.warn(
                    f"sourcing: {provider.name} contributed nothing — {reason}, and "
                    "nothing for this query is cached. The result set is incomplete.",
                    stacklevel=2,
                )
            continue

        try:
            found = provider.search(query)
        except ProviderUnavailable as exc:
            warnings.warn(
                f"sourcing: {provider.name} failed ({exc}); the result set is incomplete.",
                stacklevel=2,
            )
            continue

        digest = cache.put(
            json.dumps([o.__dict__ for o in found], sort_keys=True).encode("utf-8")
        )
        cache.record(
            CacheEntry(
                key=key,
                sha256=digest,
                media_type="application/json",
                source_url=provider.name,
                fetched_at=now,
                note=f"{len(found)} offers for {query.text!r}",
            )
        )
        offers.extend(found)

    return offers
