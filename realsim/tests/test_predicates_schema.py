"""Predicate closed-library validation (r2s.core.schemas.task)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from r2s.core.schemas.task import MAX_DEPTH, MAX_LEAVES, Predicate


def _leaf(op="on", args=("mug_0", "table_0"), **kwargs):
    return Predicate(op=op, args=list(args), kwargs=kwargs)


class TestLeafValidation:
    def test_near_without_d_max_rejected(self):
        with pytest.raises(ValidationError, match="explicit d_max"):
            Predicate(op="near", args=["mug_0", "bowl_0"])

    def test_near_with_d_max_accepted(self):
        p = Predicate(op="near", args=["mug_0", "bowl_0"], kwargs={"d_max": 0.1})
        assert p.kwargs["d_max"] == 0.1
        assert p.depth() == 0
        assert p.n_leaves() == 1

    def test_unknown_op_rejected(self):
        with pytest.raises(ValidationError, match="library is closed"):
            Predicate(op="touching", args=["a", "b"])

    def test_leaf_cannot_have_children(self):
        with pytest.raises(ValidationError, match="cannot have children"):
            Predicate(op="on", args=["a", "b"], children=[_leaf()])

    @pytest.mark.parametrize("op", ["on", "inside", "grasped", "pose_within"])
    def test_all_library_leaves_construct(self, op):
        assert _leaf(op=op).op == op


class TestLogicValidation:
    def test_logic_op_needs_children(self):
        with pytest.raises(ValidationError, match="needs children"):
            Predicate(op="and")

    def test_not_takes_exactly_one_child(self):
        with pytest.raises(ValidationError, match="exactly one child"):
            Predicate(op="not", children=[_leaf(), _leaf(op="grasped", args=("mug_0",))])

    def test_not_with_one_child_accepted(self):
        p = Predicate(op="not", children=[_leaf()])
        assert p.depth() == 1
        assert p.n_leaves() == 1

    def test_depth_greater_than_max_rejected(self):
        assert MAX_DEPTH == 2
        inner = Predicate(op="not", children=[Predicate(op="not", children=[_leaf()])])
        assert inner.depth() == 2  # legal at exactly MAX_DEPTH
        with pytest.raises(ValidationError, match="deeper than 2"):
            Predicate(op="and", children=[inner])

    def test_more_than_max_leaves_rejected(self):
        assert MAX_LEAVES == 3
        four = [
            _leaf(args=("a", "t")),
            _leaf(args=("b", "t")),
            _leaf(args=("c", "t")),
            _leaf(args=("d", "t")),
        ]
        with pytest.raises(ValidationError, match="more than 3 leaves"):
            Predicate(op="and", children=four)

    def test_valid_composition_passes(self):
        p = Predicate(
            op="and",
            children=[
                _leaf(op="on", args=("mug_0", "table_0")),
                Predicate(
                    op="not",
                    children=[_leaf(op="on", args=("mug_0", "FLOOR"))],
                ),
                Predicate(op="near", args=["mug_0", "bowl_0"], kwargs={"d_max": 0.15}),
            ],
        )
        assert p.depth() == 2
        assert p.n_leaves() == 3
        assert len(p.leaves()) == 3


class TestEntityIds:
    def test_collects_across_leaves(self):
        p = Predicate(
            op="or",
            children=[
                _leaf(op="on", args=("mug_0", "table_0")),
                _leaf(op="grasped", args=("bottle_1",)),
            ],
        )
        assert p.entity_ids() == {"mug_0", "table_0", "bottle_1"}

    def test_floor_excluded(self):
        p = _leaf(op="on", args=("mug_0", "FLOOR"))
        assert p.entity_ids() == {"mug_0"}

    def test_single_leaf(self):
        assert _leaf().entity_ids() == {"mug_0", "table_0"}
