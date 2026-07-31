"""Unit tests for agent/prompts/planner_prompt.py."""

from __future__ import annotations

from app.agent.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT, build_planner_user_prompt
from app.agent.prompts.verifier_prompt import VERIFIER_SYSTEM_PROMPT

MANIFEST = [
    {
        "name": "lookup_jurisdiction_rule",
        "description": "Deterministic lookup.",
        "args_schema": {"type": "object", "properties": {"country": {"type": "string"}}},
    }
]


class TestPlannerSystemPrompt:
    def test_states_the_planner_never_executes(self) -> None:
        assert "never execute" in PLANNER_SYSTEM_PROMPT.lower()

    def test_requires_exactly_one_jurisdiction_and_score_step(self) -> None:
        assert "lookup_jurisdiction_rule" in PLANNER_SYSTEM_PROMPT
        assert "score_lead" in PLANNER_SYSTEM_PROMPT
        assert "exactly one" in PLANNER_SYSTEM_PROMPT.lower()

    def test_treats_enquiry_text_as_untrusted_data(self) -> None:
        assert "untrusted" in PLANNER_SYSTEM_PROMPT.lower()

    def test_shares_no_text_with_the_verifier_prompt(self) -> None:
        """docs/architecture.md section 9: the verifier must be genuinely
        independent -- no shared prompt text with the planner."""
        planner_lines = {line.strip() for line in PLANNER_SYSTEM_PROMPT.splitlines() if line.strip()}
        verifier_lines = {line.strip() for line in VERIFIER_SYSTEM_PROMPT.splitlines() if line.strip()}
        assert planner_lines.isdisjoint(verifier_lines)


class TestBuildPlannerUserPrompt:
    def test_includes_the_enquiry_text_verbatim(self) -> None:
        prompt = build_planner_user_prompt("Hi, my email is jane@example.com", MANIFEST)
        assert "Hi, my email is jane@example.com" in prompt

    def test_includes_the_tool_manifest_as_json(self) -> None:
        prompt = build_planner_user_prompt("enquiry", MANIFEST)
        assert "lookup_jurisdiction_rule" in prompt
        assert '"country"' in prompt

    def test_without_correction_note_has_no_correction_section(self) -> None:
        prompt = build_planner_user_prompt("enquiry", MANIFEST)
        assert "CORRECTION REQUIRED" not in prompt

    def test_with_correction_note_appends_it(self) -> None:
        prompt = build_planner_user_prompt("enquiry", MANIFEST, correction_note="score_lead is missing.")
        assert "CORRECTION REQUIRED" in prompt
        assert "score_lead is missing." in prompt

    def test_correction_note_appears_after_the_base_content(self) -> None:
        prompt = build_planner_user_prompt("enquiry", MANIFEST, correction_note="fix this")
        assert prompt.index("enquiry") < prompt.index("fix this")

    def test_does_not_mutate_the_manifest_argument(self) -> None:
        manifest_copy = [dict(entry) for entry in MANIFEST]
        build_planner_user_prompt("enquiry", MANIFEST)
        assert MANIFEST == manifest_copy
