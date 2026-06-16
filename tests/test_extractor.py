from meeting_automation.config import Settings
from meeting_automation.extractor import extract_meeting_analysis


def test_fallback_extractor_finds_action_items() -> None:
    settings = Settings(
        openai_api_key=None,
        openai_model="test",
        jira_base_url=None,
        jira_email=None,
        jira_api_token=None,
        jira_project_key=None,
        jira_issue_type="Task",
        slack_webhook_url=None,
        organization_name="Test",
    )
    notes = """
    Decisions:
    - Use the existing Jira project.

    Action items:
    - Asha will draft validation copy by 2026-06-17.
    - Ravi will confirm labels by 2026-06-18.

    Risks:
    - Onboarding may slip.
    """

    analysis = extract_meeting_analysis(notes, settings, "Test Meeting")

    assert analysis.extraction_method == "deterministic_fallback"
    assert len(analysis.action_items) == 2
    assert analysis.action_items[0].owner == "Asha"
    assert analysis.action_items[0].due_date == "2026-06-17"
    assert analysis.action_items[0].priority == "Medium"
    assert analysis.decisions == ["Use the existing Jira project."]
    assert analysis.risks == ["Onboarding may slip."]
