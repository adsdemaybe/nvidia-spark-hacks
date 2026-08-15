"""
Typed contracts between the agents and the harness.

Every agent output is a pydantic model. This is the seam that keeps the loop
honest: an agent can only ever hand back fields the harness knows how to apply
and validate, so it cannot assert that a design passes — it can only propose a
change that the harness then measures.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Diagnosis(BaseModel):
    """The analyst's read of why the current design fails."""

    root_cause: str = Field(
        description="The single most important physical reason the design "
                    "fails, in one or two sentences. Name the mechanism, not "
                    "the symptom.")
    blocking_criteria: list[str] = Field(
        default_factory=list,
        description="Names of the failing criteria this root cause explains.")
    ruled_out: list[str] = Field(
        default_factory=list,
        description="Hypotheses considered and rejected, with the evidence "
                    "that rejected them.")
    confidence: Literal["low", "medium", "high"] = "medium"


class Change(BaseModel):
    """One edit to one design variable."""

    variable: str = Field(description="Design variable or discrete choice name")
    value: float | str = Field(description="Proposed new value")
    rationale: str = Field(description="Why this specific value, and what "
                                       "physical quantity it moves")


class Proposal(BaseModel):
    """A candidate design revision."""

    changes: list[Change] = Field(min_length=1, max_length=4)
    expected_effect: str = Field(
        description="What the harness should measure if this is right — name "
                    "the criterion and the direction it should move.")
    risks: list[str] = Field(
        default_factory=list,
        description="What this change might break, especially criteria that "
                    "currently pass.")


class Critique(BaseModel):
    """An adversarial review of a proposal."""

    verdict: Literal["accept", "revise", "reject"]
    reasoning: str = Field(description="The strongest argument against the "
                                       "proposal, or why it survives scrutiny")
    violated_constraints: list[str] = Field(
        default_factory=list,
        description="Physical or catalogue constraints the proposal breaks — "
                    "e.g. a part that cannot be bought, a bound exceeded")
    suggested_revision: str | None = None


class RunSummary(BaseModel):
    """Human-facing summary of a converged (or stalled) run."""

    outcome: Literal["converged", "stalled", "failed"]
    headline: str
    what_changed: list[str]
    what_to_verify_physically: list[str] = Field(
        default_factory=list,
        description="Values that came from inference rather than a datasheet, "
                    "or that a simulation cannot confirm.")
