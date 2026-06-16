from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Settings
from .models import JiraTicketResult, MeetingAnalysis, SlackPostResult


def build_slack_payload(
    analysis: MeetingAnalysis,
    jira_results: list[JiraTicketResult],
    meeting_title: str,
) -> dict:
    ticket_lines = []
    for result in jira_results:
        if result.created and result.url:
            ticket_lines.append(f"- <{result.url}|{result.key}>: {result.action_title}")
        elif result.error:
            ticket_lines.append(f"- Failed: {result.action_title} ({result.error})")
        else:
            ticket_lines.append(f"- {result.action_title}")

    decisions = "\n".join(f"- {item}" for item in analysis.decisions) or "- No explicit decisions captured."
    risks = "\n".join(f"- {item}" for item in analysis.risks) or "- No explicit risks captured."
    tickets = "\n".join(ticket_lines) or "- No action items found."

    return {
        "text": f"Meeting automation summary for {meeting_title}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{meeting_title} - Follow-up Summary"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary*\n{analysis.summary}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Jira tickets created*\n{tickets}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Decisions*\n{decisions}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Risks / watchouts*\n{risks}"},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Extraction method: `{analysis.extraction_method}`",
                    }
                ],
            },
        ],
    }


def write_run_report(
    output_dir: Path,
    settings: Settings,
    meeting_title: str,
    analysis: MeetingAnalysis,
    jira_results: list[JiraTicketResult],
    slack_result: SlackPostResult,
    mode: str,
) -> None:
    created = [result for result in jira_results if result.created]
    failed = [result for result in jira_results if not result.created]
    now = datetime.now().isoformat(timespec="seconds")

    lines = [
        f"# Automation Run Report",
        "",
        f"- Meeting: {meeting_title}",
        f"- Organization: {settings.organization_name}",
        f"- Run time: {now}",
        f"- Mode: {mode}",
        f"- Extraction method: {analysis.extraction_method}",
        f"- Action items found: {len(analysis.action_items)}",
        f"- Jira tickets created: {len(created)}",
        f"- Jira failures: {len(failed)}",
        f"- Slack posted: {slack_result.posted}",
        "",
        "## Tickets",
    ]

    if jira_results:
        for result in jira_results:
            status = "created" if result.created else "failed"
            target = f" ({result.url})" if result.url else ""
            error = f" Error: {result.error}" if result.error else ""
            lines.append(f"- {status}: {result.key or 'no-key'} - {result.action_title}{target}{error}")
    else:
        lines.append("- No Jira tickets were needed.")

    lines.extend(["", "## Decisions"])
    lines.extend([f"- {decision}" for decision in analysis.decisions] or ["- None captured."])

    lines.extend(["", "## Risks"])
    lines.extend([f"- {risk}" for risk in analysis.risks] or ["- None captured."])

    output_dir.joinpath("run_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
