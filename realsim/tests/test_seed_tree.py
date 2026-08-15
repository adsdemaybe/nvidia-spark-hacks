"""RunContext.rng seed-tree contracts (r2s.core.runctx)."""
from __future__ import annotations

import numpy as np
import pytest

from r2s.core.runctx import RunContext
from r2s.core.store.cas import CASStore


@pytest.fixture
def ctx(tmp_path):
    return RunContext(
        run_id="run_seed_test",
        store=CASStore(f"file://{tmp_path}/store"),
        seed=1337,
        work_dir=tmp_path / "work",
    )


class TestSeedTree:
    def test_same_scope_identical_sequences(self, ctx):
        a = ctx.rng("place", "scene_0").random(16)
        b = ctx.rng("place", "scene_0").random(16)
        np.testing.assert_array_equal(a, b)

    def test_different_scope_different_sequences(self, ctx):
        a = ctx.rng("place", "scene_0").random(16)
        b = ctx.rng("place", "scene_1").random(16)
        assert not np.array_equal(a, b)

    def test_different_stage_different_sequences(self, ctx):
        a = ctx.rng("place").random(16)
        b = ctx.rng("tasks").random(16)
        assert not np.array_equal(a, b)

    def test_int_scope_components(self, ctx):
        a = ctx.rng("cousin", 0).random(16)
        b = ctx.rng("cousin", 1).random(16)
        a2 = ctx.rng("cousin", 0).random(16)
        assert not np.array_equal(a, b)
        np.testing.assert_array_equal(a, a2)

    def test_master_seed_changes_all_streams(self, tmp_path):
        store = CASStore(f"file://{tmp_path}/store2")
        ctx_a = RunContext(run_id="r", store=store, seed=1, work_dir=tmp_path / "w")
        ctx_b = RunContext(run_id="r", store=store, seed=2, work_dir=tmp_path / "w")
        assert not np.array_equal(
            ctx_a.rng("place").random(16), ctx_b.rng("place").random(16)
        )

    def test_streams_are_independent_generators(self, ctx):
        # drawing from one stream must not perturb another
        g1 = ctx.rng("a")
        g2 = ctx.rng("b")
        _ = g1.random(100)
        after_draws = g2.random(16)
        fresh = ctx.rng("b").random(16)
        np.testing.assert_array_equal(after_draws, fresh)

    def test_global_numpy_state_untouched(self, ctx):
        np.random.seed(0)
        before = np.random.get_state()[1].copy()
        _ = ctx.rng("scope").random(100)
        after = np.random.get_state()[1]
        np.testing.assert_array_equal(before, after)

    def test_returns_generator(self, ctx):
        assert isinstance(ctx.rng("x"), np.random.Generator)
