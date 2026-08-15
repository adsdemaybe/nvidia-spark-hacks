import pytest
from pydantic import ValidationError

from engine.examples import simple_rover
from engine.ir import (
    CatalogueParam,
    GeometrySpec,
    Joint,
    Link,
    Pose,
    Provenance,
    Quantity,
    RobotIR,
)


def _link(link_id: str) -> Link:
    """A minimal valid link, for topology tests that don't care about geometry."""
    return Link(
        id=link_id,
        geometry=GeometrySpec(
            generator="plate",
            params={
                name: Quantity(
                    value=value,
                    unit="m",
                    provenance=Provenance(status="ASSUMED", source="test fixture"),
                )
                for name, value in (("length", 0.1), ("width", 0.05), ("thickness", 0.003))
            },
            material=CatalogueParam(value="pla", catalogue="materials"),
        ),
    )


def test_simple_rover_validates():
    ir = simple_rover()
    assert ir.root_link == "chassis"
    assert {link.id for link in ir.links} == {"chassis", "bracket_L", "wheel_L"}


def test_confirmed_provenance_requires_source():
    with pytest.raises(ValidationError):
        Provenance(status="CONFIRMED", source="")


def test_assumed_provenance_allows_empty_source():
    Provenance(status="ASSUMED", source="", note="chosen by us")


def test_duplicate_link_ids_rejected():
    link = Link(
        id="a",
        geometry=GeometrySpec(
            generator="plate",
            params={},
            material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
        ),
    )
    with pytest.raises(ValidationError):
        RobotIR(name="bad", root_link="a", links=[link, link.model_copy()])


def test_joint_must_reference_known_links():
    link = Link(
        id="a",
        geometry=GeometrySpec(
            generator="plate",
            params={},
            material=CatalogueParam(value="aluminum_6061", catalogue="materials"),
        ),
    )
    bad_joint = Joint(id="j", kind="fixed", parent="a", child="nonexistent")
    with pytest.raises(ValidationError):
        RobotIR(name="bad", root_link="a", links=[link], joints=[bad_joint])


def test_fixed_joint_rejects_limits():
    from engine.ir import JointLimits

    limits = JointLimits(
        lower=Quantity(value=0, unit="rad", provenance=Provenance(status="ASSUMED", source="")),
        upper=Quantity(value=1, unit="rad", provenance=Provenance(status="ASSUMED", source="")),
        effort=Quantity(value=1, unit="N*m", provenance=Provenance(status="ASSUMED", source="")),
        velocity=Quantity(value=1, unit="rad/s", provenance=Provenance(status="ASSUMED", source="")),
    )
    with pytest.raises(ValidationError):
        Joint(id="j", kind="fixed", parent="a", child="b", limits=limits)


def test_content_hash_stable_and_sensitive_to_change():
    ir = simple_rover()
    h1 = ir.content_hash()
    h2 = simple_rover().content_hash()
    assert h1 == h2  # same content, different random `id` (excluded from the hash)

    mutated = ir.model_copy(deep=True)
    mutated.links[0].geometry.params["length"] = Quantity(
        value=999.0, unit="m", provenance=Provenance(status="ASSUMED", source="")
    )
    assert mutated.content_hash() != h1


def test_revision_requires_author_shape():
    ir = simple_rover()
    from uuid import uuid4

    from engine.ir import Revision

    with pytest.raises(ValidationError):
        Revision(design_id=uuid4(), revision_no=0, ir=ir, author="nobody")

    rev = Revision(design_id=uuid4(), revision_no=0, ir=ir, author="agent:claude")
    assert rev.ir_hash == ir.content_hash()


# --- regressions: the joint graph must be a tree ---


def test_duplicate_joint_ids_are_rejected():
    links = [_link("a"), _link("b"), _link("c")]
    with pytest.raises(ValidationError, match="duplicate joint ids"):
        RobotIR(
            name="dup", root_link="a", links=links,
            joints=[
                Joint(id="j1", kind="fixed", parent="a", child="b"),
                Joint(id="j1", kind="fixed", parent="a", child="c"),
            ],
        )


def test_a_link_may_have_only_one_parent_joint():
    """Two joints claiming the same child silently last-wins in the kinematics
    walk, so the robot evaluated is not the robot authored."""
    links = [_link("a"), _link("b"), _link("c")]
    with pytest.raises(ValidationError, match="at most one parent joint"):
        RobotIR(
            name="two-parents", root_link="a", links=links,
            joints=[
                Joint(id="j1", kind="fixed", parent="a", child="c"),
                Joint(id="j2", kind="fixed", parent="b", child="c"),
            ],
        )


def test_self_referencing_joint_is_rejected():
    with pytest.raises(ValidationError, match="to itself"):
        RobotIR(
            name="self", root_link="a", links=[_link("a")],
            joints=[Joint(id="j1", kind="fixed", parent="a", child="a")],
        )


def test_root_link_may_not_have_a_parent_joint():
    """`a -> b` plus `b -> a` gives every link exactly one parent, so the
    one-parent rule alone lets the cycle through. The root having a parent is
    the tell."""
    with pytest.raises(ValidationError, match="root has no parent"):
        RobotIR(
            name="cyc", root_link="a", links=[_link("a"), _link("b")],
            joints=[
                Joint(id="j1", kind="fixed", parent="a", child="b"),
                Joint(id="j2", kind="fixed", parent="b", child="a"),
            ],
        )
