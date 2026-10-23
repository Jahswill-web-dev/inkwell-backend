from app.schemas.consultant import ConsultantTurnDecision
from app.services.consultant_interview_orchestrator import (
    approve_decision,
    direction_prompt,
    initial_phase,
    resume_prompt,
)
from app.services.interview_context import ConsultantVoiceContext

CONTEXT = ConsultantVoiceContext(
    "Jo",
    "Practical AI",
    "How engineers can use AI without replacing professional judgement",
    ["Working engineers"],
    [{"id": "argument", "text": "The central argument"}],
)


def decision(action: str, text: str = "What is the core point?") -> ConsultantTurnDecision:
    return ConsultantTurnDecision.model_validate({"action": action, "spoken_text": text})


def test_new_and_legacy_interviews_choose_safe_initial_phases() -> None:
    assert initial_phase(False, None) == "awaiting_readiness"
    assert initial_phase(True, None) == "awaiting_direction_confirmation"
    assert initial_phase(True, "adaptive_interview") == "adaptive_interview"


def test_readiness_gate_rejects_a_framing_question() -> None:
    approved = approve_decision(
        "awaiting_readiness", {}, decision("ask_framing"), CONTEXT, "Yes"
    )

    assert approved.phase == "awaiting_readiness"
    assert approved.decision.action == "ask_readiness"
    assert "core point" not in approved.decision.spoken_text


def test_clear_readiness_presents_direction_before_framing() -> None:
    approved = approve_decision(
        "awaiting_readiness", {}, decision("present_direction", "ignored"), CONTEXT, "Yes"
    )

    assert approved.phase == "awaiting_direction_confirmation"
    assert approved.decision.spoken_text == direction_prompt(CONTEXT)
    assert "Is that accurate" in approved.decision.spoken_text


def test_readiness_cannot_advance_on_a_negative_answer() -> None:
    approved = approve_decision(
        "awaiting_readiness", {}, decision("present_direction", "ignored"), CONTEXT, "No, later."
    )

    assert approved.phase == "awaiting_readiness"
    assert approved.decision.action == "ask_readiness"


def test_direction_gate_rejects_adaptive_question_but_accepts_confirmation() -> None:
    rejected = approve_decision(
        "awaiting_direction_confirmation", {}, decision("ask_adaptive"), CONTEXT, "Yes"
    )
    accepted = approve_decision(
        "awaiting_direction_confirmation",
        {},
        decision("ask_framing"),
        CONTEXT,
        "Yes, focus on construction engineers.",
    )

    assert rejected.phase == "awaiting_direction_confirmation"
    assert rejected.decision.action == "ask_direction_confirmation"
    assert accepted.phase == "framing"
    assert accepted.state["confirmed_framing"] == "Yes, focus on construction engineers."
    assert accepted.state["direction_confirmed_or_corrected"] is True


def test_direction_and_recap_require_clear_participant_confirmation() -> None:
    direction = approve_decision(
        "awaiting_direction_confirmation", {}, decision("ask_framing"), CONTEXT, "Not sure."
    )
    recap = approve_decision(
        "awaiting_recap_confirmation",
        {"latest_recap": "Recap"},
        ConsultantTurnDecision(
            action="finish",
            spoken_text="Thanks.",
            finish_reason="questions_complete",
        ),
        CONTEXT,
        "Something is missing.",
    )

    assert direction.phase == "awaiting_direction_confirmation"
    assert direction.decision.action == "ask_direction_confirmation"
    assert recap.phase == "awaiting_recap_confirmation"
    assert recap.decision.action == "clarify"


def test_resume_prompt_preserves_the_recorded_phase() -> None:
    assert "Is that accurate" in resume_prompt(
        "awaiting_direction_confirmation", CONTEXT, {}
    )
    assert "direction" not in resume_prompt("adaptive_interview", CONTEXT, {}).lower()
    assert "Here is my recap" in resume_prompt(
        "awaiting_recap_confirmation",
        CONTEXT,
        {"latest_recap": "Here is my recap. Is it accurate?"},
    )


def test_framing_question_limit_forces_adaptive_interview() -> None:
    approved = approve_decision(
        "framing",
        {"framing_questions_asked": 3},
        decision("ask_framing", "A fourth framing question?"),
        CONTEXT,
        "Previous answer",
    )

    assert approved.phase == "adaptive_interview"
    assert approved.decision.action == "ask_adaptive"


def test_final_contribution_and_recap_are_required_for_normal_completion() -> None:
    final_question = approve_decision(
        "adaptive_interview", {}, decision("ask_final_contribution"), CONTEXT, "Answer"
    )
    premature = approve_decision(
        "awaiting_final_contribution",
        final_question.state,
        ConsultantTurnDecision(
            action="finish",
            spoken_text="Thanks.",
            finish_reason="questions_complete",
        ),
        CONTEXT,
        "Nothing else",
    )
    recap = approve_decision(
        "awaiting_final_contribution",
        final_question.state,
        decision("present_recap", "Here is the recap. Is that accurate?"),
        CONTEXT,
        "Nothing else",
    )
    completed = approve_decision(
        "awaiting_recap_confirmation",
        recap.state,
        ConsultantTurnDecision(
            action="finish",
            spoken_text="Thank you.",
            finish_reason="questions_complete",
        ),
        CONTEXT,
        "Yes, that is accurate.",
    )

    assert final_question.phase == "awaiting_final_contribution"
    assert "anything else" in final_question.decision.spoken_text
    assert premature.phase == "awaiting_final_contribution"
    assert recap.phase == "awaiting_recap_confirmation"
    assert completed.phase == "completed"
    assert completed.state["recap_confirmed"] is True


def test_explicit_finish_is_allowed_from_any_phase() -> None:
    approved = approve_decision(
        "awaiting_direction_confirmation",
        {},
        ConsultantTurnDecision(
            action="finish",
            spoken_text="Thank you.",
            finish_reason="participant_finished",
        ),
        CONTEXT,
        "Please finish now.",
    )

    assert approved.phase == "completed"
    assert approved.state["completion_reason"] == "participant_finished"


def test_model_cannot_invent_an_explicit_finish_request() -> None:
    approved = approve_decision(
        "adaptive_interview",
        {},
        ConsultantTurnDecision(
            action="finish",
            spoken_text="Thank you.",
            finish_reason="participant_finished",
        ),
        CONTEXT,
        "Here is another example.",
    )

    assert approved.phase == "adaptive_interview"
    assert approved.decision.action == "clarify"

    negated = approve_decision(
        "adaptive_interview",
        {},
        ConsultantTurnDecision(
            action="finish",
            spoken_text="Thank you.",
            finish_reason="participant_finished",
        ),
        CONTEXT,
        "I am not done; please keep going.",
    )
    assert negated.phase == "adaptive_interview"


def test_ambiguity_and_optional_followups_are_persisted_and_limited() -> None:
    clarified = approve_decision(
        "adaptive_interview",
        {},
        ConsultantTurnDecision(
            action="clarify",
            spoken_text="When you said 'cost schedule', what did you mean?",
            ambiguous_phrase="cost schedule",
        ),
        CONTEXT,
        "We use a cost schedule.",
    )
    too_many = approve_decision(
        "adaptive_interview",
        {"optional_followup_counts": {"argument": 2}, "covered_goal_ids": ["argument"]},
        ConsultantTurnDecision(
            action="ask_adaptive",
            spoken_text="One more?",
            goal_id="argument",
        ),
        CONTEXT,
        "Answer",
    )

    assert clarified.state["unresolved_ambiguity"] == {
        "phrase": "cost schedule",
        "return_phase": "adaptive_interview",
    }
    assert "cost schedule" in clarified.decision.spoken_text
    assert too_many.phase == "awaiting_final_contribution"
    assert too_many.decision.action == "ask_final_contribution"


