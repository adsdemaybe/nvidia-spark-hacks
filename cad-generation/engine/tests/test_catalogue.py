import pytest

from engine.catalogue import CATALOGUES, MATERIALS, resolve


def test_unknown_key_names_the_catalogue_and_its_keys():
    """§11 non-negotiable #1: nobody may invent a part. The KeyError has to say
    which catalogue and what was actually in it, or the agent's only recovery is
    to guess again."""
    with pytest.raises(KeyError) as excinfo:
        MATERIALS["unobtanium"]
    msg = str(excinfo.value)
    assert "unobtanium" in msg
    assert "materials" in msg
    assert "aluminum_6061" in msg


def test_catalogues_are_iterable():
    """`list(catalogue)` used to hit the legacy sequence protocol and raise
    "0 not in catalogue 'gearboxes'" — a missing __iter__ reported as a corrupt
    catalogue."""
    for name, catalogue in CATALOGUES.items():
        keys = list(catalogue)
        assert keys == catalogue.keys() == sorted(keys)
        assert len(catalogue) == len(keys)
        assert all(k in catalogue for k in keys), name


def test_resolve_goes_through_the_catalogue():
    spec = resolve("materials", "aluminum_6061")
    assert spec.density.value > 0
    with pytest.raises(KeyError):
        resolve("materials", "aluminum_6062")
