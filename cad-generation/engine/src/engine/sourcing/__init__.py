"""§6 — real parts and real models, fetched not typed.

The catalogue is only as good as the table behind it. `CatalogueParam` stops an
optimizer inventing a 7.5:1 gearbox, but a made-up torque on a real part number
fails identically and later, when the hardware arrives. This package is the
answer: the table is built from the real world, cached on disk, and stamped with
provenance on everything it touches.

Four rules hold across every module here, and they are the reason the package is
separate from `engine.catalogue` rather than folded into it:

1. **The network is touched at catalogue-build time only.** `evaluate()` and the
   optimizer never import this package. Local-first is a hard constraint, and an
   evaluation that depends on a distributor's uptime is not an evaluation.
2. **Absence of API keys degrades to cache-only, loudly.** Not to an exception,
   which would make a laptop with no keys unable to run anything; not to
   silence, which would make a stale cache indistinguishable from a fresh fetch.
3. **Everything fetched is content-addressed.** A part's datasheet, a vendor's
   STEP, an API response — keyed by the sha256 of the bytes, so "the same
   evaluation" means the same bytes and not the same URL.
4. **An agent never types a number in.** An agent may *propose* a part or
   *request* a search ("NEMA17 class, >=0.4 N*m at speed, <=350 g"). The fetch,
   the parse, the normalisation and the cross-check are tools; the librarian
   extracts with citations; a human confirms. §6.3, and §12 non-negotiable #9.

The one rule this package cannot enforce on its own is #4, because nothing stops
a human editing `catalogue.py`. What it does instead is make the debt countable:
`catalogue.unsourced_entries()` lists every value that has not been through this
path, so "we should source that properly" is a list rather than a feeling.
"""

from engine.sourcing.cache import CacheEntry, SourcingCache, cache_root
from engine.sourcing.librarian import (
    Citation,
    ExtractedValue,
    confirm,
    to_quantity,
)
from engine.sourcing.models import (
    IngestResult,
    ModelProvenance,
    VisualOnlyError,
    ingest_model,
    placeholder_from_dimensions,
)
from engine.sourcing.providers import (
    OFFLINE_ENV,
    PartOffer,
    PartQuery,
    Provider,
    ProviderUnavailable,
    available_providers,
    search,
)

__all__ = [
    "CacheEntry",
    "Citation",
    "ExtractedValue",
    "IngestResult",
    "ModelProvenance",
    "OFFLINE_ENV",
    "PartOffer",
    "PartQuery",
    "Provider",
    "ProviderUnavailable",
    "SourcingCache",
    "VisualOnlyError",
    "available_providers",
    "cache_root",
    "confirm",
    "ingest_model",
    "placeholder_from_dimensions",
    "search",
    "to_quantity",
]
