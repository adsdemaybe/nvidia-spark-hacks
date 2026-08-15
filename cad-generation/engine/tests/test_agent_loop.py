import pytest

from engine.agent_loop import (
    Prediction,
    Proposal,
    _feedback_text,
    _score_predictions,
    _system_prompt,
)
from engine.evaluate import evaluate
from engine.examples import simple_rover


def test_system_prompt_lists_generators_and_catalogues():
    prompt = _system_prompt(max_tier=1)
    assert "tube" in prompt
    assert "aluminum_6061" in prompt
    assert "nema17_direct" in prompt
    assert "joint_torque_budget" in prompt


def test_system_prompt_omits_tier1_criterion_text_when_bounded_to_tier0():
    prompt = _system_prompt(max_tier=0)
    assert "joint_torque_budget" not in prompt


def test_proposal_schema_is_generatable():
    schema = Proposal.model_json_schema()
    assert schema["properties"].keys() == {"rationale", "robot", "predictions"}


def test_score_predictions_matches_by_criterion_name():
    report = evaluate(simple_rover())
    correct_prediction = Prediction(
        criterion_name="static_margin", predicted_passed=True, predicted_magnitude=0.9
    )
    wrong_prediction = Prediction(
        criterion_name="mount_fits[chassis_to_bracket]", predicted_passed=False, predicted_magnitude=0.0
    )
    scored = _score_predictions([correct_prediction, wrong_prediction], report)
    assert scored["static_margin"] is True
    assert scored["mount_fits[chassis_to_bracket]"] is False


def test_score_predictions_ignores_unknown_criterion_names():
    report = evaluate(simple_rover())
    unknown = Prediction(criterion_name="does_not_exist", predicted_passed=True, predicted_magnitude=1.0)
    assert _score_predictions([unknown], report) == {}


def test_feedback_text_flags_wrong_predictions_and_asks_for_revision_on_failure():
    ir = simple_rover()
    joint = next(j for j in ir.joints if j.id == "chassis_to_bracket")
    joint.origin.position.x = 5.0  # break mount_fits
    report = evaluate(ir)
    scored = _score_predictions(
        [Prediction(criterion_name="mount_fits[chassis_to_bracket]", predicted_passed=True, predicted_magnitude=0.5)],
        report,
    )
    text = _feedback_text(report, scored)
    assert "FAIL" in text
    assert "your prediction was WRONG" in text
    assert "Revise the design" in text


def test_feedback_text_has_no_revision_prompt_when_passing():
    report = evaluate(simple_rover())
    text = _feedback_text(report, {})
    assert "Revise the design" not in text


def test_proposal_validates_a_full_robot_ir_round_trip():
    ir = simple_rover()
    proposal = Proposal(
        rationale="test",
        robot=ir,
        predictions=[Prediction(criterion_name="static_margin", predicted_passed=True, predicted_magnitude=0.9)],
    )
    # round-trip through the same JSON shape a tool_use.input would carry
    payload = proposal.model_dump(mode="json")
    reloaded = Proposal.model_validate(payload)
    assert reloaded.robot.name == ir.name
    assert reloaded.robot.content_hash() == ir.content_hash()


def test_endpoint_that_ignores_structured_output_fails_nameably(monkeypatch):
    """A server that accepts `response_format` and ignores it must be named, not
    silently retried until the iteration budget runs out.

    llama.cpp does exactly this, and so does vLLM built without a guided-decoding
    backend. Prose cannot come out of a constrained decode, so the first
    non-`{` reply is a fact about the endpoint, not an answer the model can
    revise. Left to the reject path it burned every iteration re-asking a server
    that would never comply and returned an empty list, which is also what a
    model that simply designs badly returns.
    """
    import engine.agent_loop as agent_loop

    class _Message:
        tool_calls = None
        content = "Sure! Here is a rover design in JSON: {\"links\": ..."

    class _Response:
        choices = [type("C", (), {"message": _Message(), "finish_reason": "stop"})()]

    class _FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _Response()

    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _FakeClient())

    with pytest.raises(RuntimeError) as excinfo:
        agent_loop.run_design_loop(
            "a rover",
            max_tier=0,
            max_iterations=1,
            provider="qwen",
            model="whatever",
            base_url="http://127.0.0.1:9999/v1",
        )
    msg = str(excinfo.value)
    assert "ignored response_format" in msg
    assert "structured outputs" in msg
    assert "http://127.0.0.1:9999/v1" in msg
    assert "Here is a rover design" in msg  # what it said instead
