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
