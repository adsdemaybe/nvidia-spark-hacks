"""Datasheet extraction with a deterministic leash (§6.1).

    "A `librarian` agent extracts values from the PDF into a catalogue entry,
    every value cites page/figure, and the entry lands as **INFERRED**.
    Promotion to CONFIRMED requires a human check of the cited figure — a
    checkbox per value, minutes of work, and the only place a human number
    review exists in the loop."

This module is the leash, not the agent. It defines what an extraction has to
look like to be accepted at all, and the shape is chosen so that the two ways an
extraction can be wrong are separable:

- **The value is wrong.** The agent read 40 where the sheet says 43. A citation
  makes this a thirty-second check against a specific figure on a specific page,
  which is what makes the human step cheap enough to actually happen.
- **The value is unfindable.** The agent produced a number the sheet does not
  contain anywhere. No citation is possible, so `ExtractedValue` will not
  construct, and the failure lands at extraction time rather than in a BOM.

The type does not let an extraction reach the catalogue as CONFIRMED. `confirm()`
is the only path, it requires a named reviewer, and there is no bulk variant —
because a "confirm all" button is how a hundred unread values become CONFIRMED
in one click, and the entire value of the ladder is that CONFIRMED means someone
looked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.ir import Provenance, Quantity
from engine.units import UnitError, compatible


@dataclass(frozen=True)
class Citation:
    """Where in a document a value was read.

    `document_sha256` and not a URL: a manufacturer who revises a datasheet in
    place has produced a different document, and a citation that still resolves
    to "page 4 of the current PDF" would be pointing at a figure that may no
    longer say what it said. The hash makes a stale citation detectable.
    """

    document_sha256: str
    page: int
    figure: str = ""  # "Table 2", "Fig. 7 torque-speed", "§4.1"
    quote: str = ""  # the text or cell as read, for the reviewer to match against

    def __post_init__(self) -> None:
        if not self.document_sha256:
            raise ValueError(
                "a citation needs the sha256 of the document it read; without it the "
                "reviewer cannot tell which revision of the datasheet was cited"
            )
        if self.page < 1:
            raise ValueError(f"page {self.page} is not a page number")

    def describe(self) -> str:
        where = f"p.{self.page}"
        if self.figure:
            where += f" {self.figure}"
        return f"{self.document_sha256[:12]}... {where}"


@dataclass(frozen=True)
class ExtractedValue:
    """One number the librarian read, with the citation that makes it checkable.

    Construction enforces what the doc asks for in prose: a unit that parses, a
    citation that resolves, and a semantic dimension the value must match. The
    last is what catches a value read out of the wrong column — a `rated_current`
    filled in from the voltage column parses, cites a real page, and is still
    wrong in a way only dimensional analysis sees.
    """

    field: str  # the catalogue field this fills, e.g. "stall_torque"
    value: float
    unit: str
    citation: Citation
    semantic: str = ""  # "torque", "current", ... checked when given
    extracted_by: str = ""  # "agent:librarian@<model>", for accuracy scoring
    note: str = ""

    def __post_init__(self) -> None:
        if self.semantic:
            try:
                ok = compatible(self.unit, self.semantic)
            except UnitError as exc:
                raise ValueError(str(exc)) from None
            if not ok:
                raise ValueError(
                    f"{self.field}: {self.value} {self.unit} is not a {self.semantic}. "
                    "This is the shape of a value read from the wrong column of a "
                    "datasheet table — check the citation before changing the unit."
                )

    @property
    def confirmed(self) -> bool:
        return False


@dataclass
class ExtractionBatch:
    """Everything one librarian pass produced for one part.

    Grouped per part rather than per value because that is how a human reviews
    it: with the datasheet open, working down the fields for one motor, not
    hopping between documents.
    """

    catalogue: str
    key: str
    document_sha256: str
    values: list[ExtractedValue] = field(default_factory=list)
    confirmed: dict[str, str] = field(default_factory=dict)  # field -> reviewer

    def pending(self) -> list[ExtractedValue]:
        return [v for v in self.values if v.field not in self.confirmed]

    def summary(self) -> str:
        lines = [
            f"{self.catalogue}/{self.key} — {len(self.values)} values, "
            f"{len(self.confirmed)} confirmed, {len(self.pending())} awaiting review"
        ]
        for v in self.values:
            mark = "[x]" if v.field in self.confirmed else "[ ]"
            who = f" — confirmed by {self.confirmed[v.field]}" if v.field in self.confirmed else ""
            lines.append(
                f"  {mark} {v.field:22s} {v.value:>12.6g} {v.unit:10s} "
                f"{v.citation.describe()}{who}"
            )
        return "\n".join(lines)


def to_quantity(extracted: ExtractedValue) -> Quantity:
    """An extracted value as an INFERRED Quantity. Never CONFIRMED.

    §6.1 is unambiguous about the landing status, and it is worth being clear
    about why INFERRED rather than CONFIRMED even when the extraction is
    obviously right: CONFIRMED is not a claim about the number, it is a claim
    that a human checked it. An agent cannot make that claim about itself.
    """
    return Quantity(
        value=extracted.value,
        unit=extracted.unit,
        provenance=Provenance(
            status="INFERRED",
            source=extracted.citation.describe(),
            note=(
                f"extracted by {extracted.extracted_by or 'the librarian'}; "
                f"quote: {extracted.citation.quote!r}"
                + (f"; {extracted.note}" if extracted.note else "")
                + " — awaiting human confirmation of the cited figure"
            ),
        ),
    )


def confirm(
    extracted: ExtractedValue,
    *,
    reviewer: str,
    resolvable_source: str,
    batch: ExtractionBatch | None = None,
) -> Quantity:
    """Promote one extracted value to CONFIRMED, on a named human's say-so.

    `reviewer` is required and must name a person. §5 makes CI fail when a
    CONFIRMED entry lacks a resolvable source; this adds the other half, which
    the doc states and nothing enforced: a CONFIRMED entry that nobody signed is
    a value that promoted itself.

    One value at a time, deliberately. There is no `confirm_all`, and there
    should not be.
    """
    if not reviewer.strip():
        raise ValueError(
            "confirming a value requires naming the human who checked the cited "
            "figure. CONFIRMED is a claim about a review, not about a number."
        )
    if reviewer.startswith("agent:"):
        raise ValueError(
            f"{reviewer!r} is an agent. §6.3: the librarian extracts, the harness "
            "checks, a human confirms — an agent may not confirm its own extraction, "
            "or any other agent's."
        )
    if not resolvable_source.strip():
        raise ValueError("CONFIRMED provenance requires a resolvable source (§5)")

    if batch is not None:
        batch.confirmed[extracted.field] = reviewer

    return Quantity(
        value=extracted.value,
        unit=extracted.unit,
        provenance=Provenance(
            status="CONFIRMED",
            source=resolvable_source,
            note=(
                f"{extracted.citation.describe()} checked by {reviewer}"
                + (f"; {extracted.note}" if extracted.note else "")
            ),
        ),
    )


@dataclass(frozen=True)
class CurveTable:
    """An extracted curve, stored as points with the source figure hashed.

    §6.1: "Extracted curves (torque-speed, discharge) are stored as point tables
    with the source figure hashed alongside." A fit would be smaller and would
    launder the reading into a number nobody can check against the figure; the
    points can be laid over the plot.
    """

    field: str
    points: list[tuple[float, float]]
    x_unit: str
    y_unit: str
    citation: Citation
    condition: str = ""  # "at 12 V", "at 1.7 A/phase"

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError(
                f"{self.field}: a curve needs at least two points; "
                f"{len(self.points)} were extracted"
            )
        xs = [p[0] for p in self.points]
        if xs != sorted(xs):
            raise ValueError(
                f"{self.field}: curve points must ascend in x; interpolation over an "
                "unsorted table silently returns the wrong value rather than failing"
            )
