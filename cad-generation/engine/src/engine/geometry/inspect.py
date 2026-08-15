"""Measure features of a solid, not just its bulk properties.

Mass properties answer "how heavy, how big, where is the centre" and cannot answer "is
that hole actually a hole". The distinction is not academic. Asked for a 12mm spacer with
a 5.2mm bore, a model wrote:

    part = Cylinder(6, 8) - Cylinder(2.6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))

The outer cylinder takes the default CENTER alignment and spans z ∈ [-4, 4]; the bore is
MIN-aligned and spans z ∈ [0, 8]. They overlap for 4mm, so the bore goes *halfway* and
stops. The part built, exported, imported and measured cleanly — 819.8 mm³, 1.02 g — and
the harness reported OK, because every number it checks was correct **for the solid that
was built**. It was simply not the part that was asked for.

Bounding box does not catch it: 12 x 12 x 8, exactly right. Volume does not catch it
unless you already know what the answer should be. What catches it is asking whether the
bore reaches the far face, which is a question about cross-sections.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SectionProfile:
    """Cross-sections taken just inside each end of a solid, along one axis."""

    axis: str
    near_area_mm2: float
    far_area_mm2: float
    near_holes: int
    far_holes: int

    @property
    def bore_is_through(self) -> bool:
        """A bore that reaches both ends leaves a hole in both sections."""
        return self.near_holes > 0 and self.far_holes > 0

    def describe(self) -> str:
        return (
            f"along {self.axis}: near face {self.near_area_mm2:.1f} mm² with "
            f"{self.near_holes} hole(s), far face {self.far_area_mm2:.1f} mm² with "
            f"{self.far_holes} hole(s)"
        )


def profile(part, axis: str = "Z", inset: float = 0.1) -> SectionProfile:
    """Section the solid just inside both ends and count holes in each face.

    `inset` keeps the cut off the end faces themselves, where a section is degenerate.
    """
    from build123d import Plane

    bb = part.bounding_box()
    lo = {"X": bb.min.X, "Y": bb.min.Y, "Z": bb.min.Z}[axis.upper()]
    hi = {"X": bb.max.X, "Y": bb.max.Y, "Z": bb.max.Z}[axis.upper()]
    span = hi - lo
    step = max(min(inset, span * 0.05), span * 1e-3)

    plane = {"X": Plane.YZ, "Y": Plane.XZ, "Z": Plane.XY}[axis.upper()]

    def cut(at: float):
        sec = part & plane.offset(at)
        faces = sec.faces()
        area = sum(f.area for f in faces)
        # An inner wire is a hole. A plain disc has one (outer) wire; a bore adds one.
        holes = sum(max(len(f.inner_wires()), 0) for f in faces)
        return area, holes

    near_area, near_holes = cut(lo + step)
    far_area, far_holes = cut(hi - step)
    return SectionProfile(axis.upper(), near_area, far_area, near_holes, far_holes)
