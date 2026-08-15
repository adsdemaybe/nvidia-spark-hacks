"""ProceduralSource contracts (scan.generate.sources)."""
from __future__ import annotations

import numpy as np
import pytest

from scan.generate.sources import ProceduralSource

CATEGORIES = sorted(ProceduralSource._RECIPES)


@pytest.fixture(scope="module")
def source():
    return ProceduralSource()


class TestEveryRecipe:
    @pytest.mark.parametrize("category", CATEGORIES)
    def test_real_geometry(self, source, category, tmp_path):
        mesh = source.produce(category, "", seed=7, cache_dir=tmp_path)
        assert len(mesh.faces) > 4, f"{category} produced degenerate geometry"
        assert all(e > 0 for e in mesh.extents), f"{category} has a zero extent"

    @pytest.mark.parametrize("category", CATEGORIES)
    def test_plausible_household_scale(self, source, category, tmp_path):
        mesh = source.produce(category, "", seed=11, cache_dir=tmp_path)
        assert max(mesh.extents) < 2.0, f"{category} larger than 2m"
        assert max(mesh.extents) > 0.01, f"{category} smaller than 1cm"

    def test_unknown_category_falls_back_to_box(self, source, tmp_path):
        mesh = source.produce("hovercraft", "", seed=3, cache_dir=tmp_path)
        assert len(mesh.faces) > 4

    def test_descriptor_recorded_in_metadata(self, source, tmp_path):
        mesh = source.produce("mug", "a chipped blue mug", seed=1, cache_dir=tmp_path)
        assert mesh.metadata["descriptor"] == "a chipped blue mug"
        default = source.produce("mug", "", seed=1, cache_dir=tmp_path)
        assert default.metadata["descriptor"] == "procedural mug"

    def test_always_available(self, source):
        ok, _ = source.available()
        assert ok


class TestDeterminism:
    @pytest.mark.parametrize("category", ["mug", "table", "box"])
    def test_same_seed_same_vertices(self, source, category, tmp_path):
        a = source.produce(category, "", seed=42, cache_dir=tmp_path)
        b = source.produce(category, "", seed=42, cache_dir=tmp_path)
        assert a.vertices.shape == b.vertices.shape
        np.testing.assert_array_equal(a.vertices, b.vertices)
        np.testing.assert_array_equal(a.faces, b.faces)

    @pytest.mark.parametrize("category", ["mug", "table", "book"])
    def test_different_seed_different_vertices(self, source, category, tmp_path):
        a = source.produce(category, "", seed=42, cache_dir=tmp_path)
        b = source.produce(category, "", seed=43, cache_dir=tmp_path)
        different = a.vertices.shape != b.vertices.shape or not np.allclose(
            a.vertices, b.vertices
        )
        assert different, f"{category}: seeds 42 and 43 produced identical geometry"
