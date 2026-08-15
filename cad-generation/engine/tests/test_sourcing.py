"""§6 — the sourcing layer: cache, offline behaviour, librarian, model ingest."""

from __future__ import annotations

import json

import pytest

from engine.sourcing.cache import CacheEntry, SourcingCache
from engine.sourcing.librarian import Citation, CurveTable, ExtractedValue, confirm, to_quantity
from engine.sourcing.models import (
    MASS_TOLERANCE,
    ModelProvenance,
    VisualOnlyError,
    check_mass,
    ingest_model,
    require_matable,
)
from engine.sourcing.providers import PartQuery, available_providers, online, search


# --- cache --------------------------------------------------------------


def test_the_cache_is_keyed_by_content_not_by_url(tmp_path):
    """A manufacturer who replaces the STEP behind a stable URL has changed the
    robot. A URL-keyed cache hides that; content addressing turns it into a new
    hash and a visible change."""
    cache = SourcingCache(tmp_path)
    a = cache.put(b"revision one")
    b = cache.put(b"revision two")
    assert a != b
    assert cache.get(a) == b"revision one"
    # Identical bytes are one object, however many times they arrive.
    assert cache.put(b"revision one") == a


def test_a_cache_object_edited_in_place_is_refused(tmp_path):
    cache = SourcingCache(tmp_path)
    digest = cache.put(b"the bytes the IR pinned")
    cache.path_for(digest).write_bytes(b"different bytes entirely")
    with pytest.raises(ValueError) as exc:
        cache.get(digest)
    assert "corrupted or edited in place" in str(exc.value)


def test_quarantine_keeps_the_evidence(tmp_path):
    cache = SourcingCache(tmp_path)
    digest = cache.put(b"a mis-scaled motor model")
    cache.record(CacheEntry(key="model:https://x/m.step", sha256=digest))
    cache.quarantine("model:https://x/m.step", "mass cross-check failed")

    assert cache.has(digest), "deleting would lose the file somebody needs to look at"
    assert [e.key for e in cache.quarantined()] == ["model:https://x/m.step"]


# --- providers ----------------------------------------------------------


def test_the_network_is_off_unless_explicitly_turned_on(monkeypatch):
    """Offline is the default, not the fallback. A module that reaches the
    network unless told not to will eventually be imported by something on the
    evaluation path."""
    monkeypatch.delenv("SOURCING_ONLINE", raising=False)
    assert not online()


def test_a_search_with_no_keys_warns_loudly_and_returns_what_it_can(tmp_path, monkeypatch):
    """§6.2: "API keys are config, absence degrades to cache-only with a loud
    warning". Silence would make a stale cache indistinguishable from a fetch."""
    monkeypatch.delenv("SOURCING_ONLINE", raising=False)
    for var in ("NEXAR_TOKEN", "DIGIKEY_ACCESS_TOKEN", "MOUSER_API_KEY", "LCSC_API_URL"):
        monkeypatch.delenv(var, raising=False)

    with pytest.warns(UserWarning) as warnings:
        offers = search(PartQuery(text="NEMA17"), cache=SourcingCache(tmp_path))

    assert offers == []
    assert len(warnings) == 4, "every provider has to account for itself"
    assert any("incomplete" in str(w.message) for w in warnings)


def test_cached_results_are_served_but_flagged_as_possibly_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCING_ONLINE", raising=False)
    monkeypatch.setenv("NEXAR_TOKEN", "")

    cache = SourcingCache(tmp_path)
    query = PartQuery(text="NEMA17")
    payload = [
        {
            "provider": "nexar", "mpn": "17HS4401", "manufacturer": "generic",
            "description": "", "datasheet_url": "", "cad_url": "", "stock": 12,
            "unit_price_usd": 9.5, "distributor_sku": "", "lifecycle": "",
            "jlc_assembly": "", "attributes": {},
        }
    ]
    digest = cache.put(json.dumps(payload).encode())
    cache.record(
        CacheEntry(key=query.cache_key("nexar"), sha256=digest, fetched_at="2026-01-01T00:00:00Z")
    )

    with pytest.warns(UserWarning) as warnings:
        offers = search(query, cache=cache)

    assert [o.mpn for o in offers] == ["17HS4401"]
    assert any("may be stale" in str(w.message) for w in warnings)


def test_no_provider_is_configured_by_accident(monkeypatch):
    for var in ("NEXAR_TOKEN", "DIGIKEY_ACCESS_TOKEN", "DIGIKEY_CLIENT_ID",
                "MOUSER_API_KEY", "LCSC_API_URL"):
        monkeypatch.delenv(var, raising=False)
    assert available_providers() == []


# --- librarian ----------------------------------------------------------


def _citation() -> Citation:
    return Citation(
        document_sha256="a" * 64, page=4, figure="Table 2", quote="Holding torque 0.40 N·m"
    )


def test_an_extraction_without_a_citation_cannot_be_constructed():
    with pytest.raises(ValueError) as exc:
        Citation(document_sha256="", page=4)
    assert "sha256 of the document" in str(exc.value)


def test_a_value_read_from_the_wrong_column_is_caught_by_its_dimension():
    with pytest.raises(ValueError) as exc:
        ExtractedValue(
            field="rated_current", value=12.0, unit="V", citation=_citation(), semantic="current"
        )
    assert "wrong column" in str(exc.value)


def test_an_extraction_lands_as_inferred_never_confirmed():
    """§6.1. CONFIRMED is not a claim about the number, it is a claim that a
    human checked it, and an agent cannot make that claim about itself."""
    value = ExtractedValue(
        field="stall_torque", value=0.40, unit="N*m", citation=_citation(),
        semantic="torque", extracted_by="agent:librarian@qwen3-coder-next",
    )
    quantity = to_quantity(value)
    assert quantity.provenance.status == "INFERRED"
    assert "awaiting human confirmation" in quantity.provenance.note
    assert "p.4 Table 2" in quantity.provenance.source


def test_an_agent_may_not_confirm_its_own_extraction():
    value = ExtractedValue(field="stall_torque", value=0.40, unit="N*m", citation=_citation())
    with pytest.raises(ValueError) as exc:
        confirm(value, reviewer="agent:librarian", resolvable_source="https://x/ds.pdf")
    assert "an agent may not confirm" in str(exc.value)


def test_confirmation_requires_a_named_human_and_a_resolvable_source():
    value = ExtractedValue(field="stall_torque", value=0.40, unit="N*m", citation=_citation())
    with pytest.raises(ValueError):
        confirm(value, reviewer="", resolvable_source="https://x/ds.pdf")
    with pytest.raises(ValueError):
        confirm(value, reviewer="alex", resolvable_source="  ")

    quantity = confirm(value, reviewer="alex", resolvable_source="https://x/ds.pdf")
    assert quantity.provenance.status == "CONFIRMED"
    assert "checked by alex" in quantity.provenance.note


def test_a_curve_must_ascend_or_interpolation_lies():
    with pytest.raises(ValueError) as exc:
        CurveTable(
            field="torque_speed", points=[(10.0, 0.1), (0.0, 0.4)],
            x_unit="rad/s", y_unit="N*m", citation=_citation(),
        )
    assert "ascend" in str(exc.value)


# --- model ingest -------------------------------------------------------


def test_the_mass_cross_check_catches_a_unit_error():
    """§6.2's worked example: "A motor model 40% off on mass is mis-scaled or
    hollow, and it's caught at ingest instead of skewing every CoM downstream."
    """
    # A 280 g NEMA17 whose model was drawn in mm and read as m.
    ok, explanation, record = check_mass(
        volume_m3=3.7e-5 * 1e9, density_kg_per_m3=7500.0, datasheet_mass_kg=0.28
    )
    assert not ok
    assert "unit error" in explanation
    assert record["error"] > MASS_TOLERANCE


def test_the_mass_cross_check_distinguishes_a_hollow_body_from_a_unit_error():
    ok, explanation, _ = check_mass(
        volume_m3=1.8e-5, density_kg_per_m3=7500.0, datasheet_mass_kg=0.28
    )
    assert not ok
    assert "shelled body" in explanation


def test_a_model_that_passes_is_still_visual_only(tmp_path):
    """§5 allows no exception. "Best available" is still visual_only."""
    result = ingest_model(
        asset_path=tmp_path / "assets" / "nema17.step",
        data=b"ISO-10303-21;",
        source_url="https://vendor.example/nema17.step",
        license="manufacturer-published",
        volume_m3=3.73e-5,
        density_kg_per_m3=7500.0,
        datasheet_mass_kg=0.28,
        cache=SourcingCache(tmp_path / "cache"),
    )
    assert result.accepted
    assert result.provenance.visual_only is True


def test_a_failed_ingest_quarantines_the_model_and_keeps_the_pipeline_moving(tmp_path):
    """"Failures quarantine the model; the part keeps a parametric placeholder
    ... so the pipeline never blocks on a pretty model." (§6.2)"""
    asset = tmp_path / "assets" / "suspicious.step"
    result = ingest_model(
        asset_path=asset,
        data=b"ISO-10303-21;",
        source_url="https://community.example/suspicious.step",
        license="manufacturer-published",
        volume_m3=1.0e-3,  # a thousand times too big
        density_kg_per_m3=7500.0,
        datasheet_mass_kg=0.28,
        datasheet_dimensions_m=(0.042, 0.042, 0.040),
        cache=SourcingCache(tmp_path / "cache"),
    )
    assert not result.accepted
    assert result.provenance.quarantined
    assert result.placeholder is not None
    assert result.placeholder["generator"] == "component"
    # The placeholder's dimensions come from the datasheet, which is where mating
    # dimensions were always supposed to come from.
    assert result.placeholder["params"]["length_m"] == 0.042


def test_an_unlicensed_model_is_recorded_not_assumed_permissive(tmp_path):
    result = ingest_model(
        asset_path=tmp_path / "assets" / "grabcad.step",
        data=b"ISO-10303-21;",
        source_url="https://grabcad.example/x",
        license="",
        cache=SourcingCache(tmp_path / "cache"),
    )
    assert not result.accepted
    assert "not in the permitted set" in result.reasons[0]


def test_a_model_nobody_cross_checked_says_so(tmp_path):
    result = ingest_model(
        asset_path=tmp_path / "assets" / "unchecked.step",
        data=b"ISO-10303-21;",
        source_url="https://vendor.example/x",
        license="manufacturer-published",
        cache=SourcingCache(tmp_path / "cache"),
    )
    assert result.accepted
    assert result.provenance.mass_check == {"skipped": True}
    assert "has not been verified" in result.provenance.note


def test_cutting_a_mating_feature_from_vendor_cad_is_a_type_error(tmp_path):
    """§5: "The rule stops being discipline and becomes a type error." """
    asset = tmp_path / "nema17.step"
    asset.write_bytes(b"ISO-10303-21;")
    ModelProvenance(
        asset="nema17.step",
        sha256="0" * 64,
        source_url="https://vendor.example/nema17.step",
        license="manufacturer-published",
    ).write(asset)

    with pytest.raises(VisualOnlyError) as exc:
        require_matable(asset)
    assert "bolt pattern" in str(exc.value)
    assert "datasheet" in str(exc.value)


def test_an_asset_with_no_ingest_sidecar_is_allowed_through(tmp_path):
    # Most of `vendor/` predates the pipeline. Breaking existing designs to
    # enforce a rule on files nobody claimed were vendor CAD is the wrong trade.
    asset = tmp_path / "legacy.step"
    asset.write_bytes(b"ISO-10303-21;")
    require_matable(asset)  # does not raise


def test_a_quarantined_model_may_not_be_used_at_all(tmp_path):
    asset = tmp_path / "bad.step"
    asset.write_bytes(b"ISO-10303-21;")
    provenance = ModelProvenance(
        asset="bad.step", sha256="0" * 64, source_url="https://x/y", license="MIT"
    )
    provenance.quarantined = True
    provenance.quarantine_reason = "mass cross-check failed"
    provenance.write(asset)

    with pytest.raises(VisualOnlyError) as exc:
        require_matable(asset)
    assert "may not be used at all" in str(exc.value)
