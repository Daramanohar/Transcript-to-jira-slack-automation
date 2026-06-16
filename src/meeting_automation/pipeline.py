from __future__ import annotations

from pathlib import Path

from .config import Settings
from .extractor import extract_meeting_analysis
from .jira_client import JiraClient
from .models import AutomationResult
from .reporting import build_slack_payload, write_run_report
from .slack_client import SlackClient
from .util import ensure_dir, write_json


def run_pipeline(
    input_path: str | Path,
    output_dir: str | Path,
    settings: Settings,
    meeting_title: str,
    mode: str,
    meeting_date: str | None = None,
) -> AutomationResult:
    transcript = Path(input_path).read_text(encoding="utf-8")
    output = ensure_dir(output_dir)

    analysis = extract_meeting_analysis(
        transcript=transcript,
        settings=settings,
        meeting_title=meeting_title,
        meeting_date=meeting_date,
    )

    if mode == "live" and not settings.jira_ready:
        raise RuntimeError("Live mode requires Jira credentials in .env.")
    if mode == "live" and not settings.slack_ready:
        raise RuntimeError("Live mode requires SLACK_WEBHOOK_URL in .env.")

    force_dry_run = mode == "dry-run"
    jira_client = JiraClient(settings=settings, dry_run=force_dry_run)
    jira_results, jira_payloads = jira_client.create_many(analysis.action_items)

    slack_payload = build_slack_payload(analysis, jira_results, meeting_title)
    slack_client = SlackClient(settings=settings, dry_run=force_dry_run)
    slack_result = slack_client.post(slack_payload)

    write_json(output / "analysis.json", analysis.to_dict())
    write_json(output / "jira_payloads.json", jira_payloads)
    write_json(output / "jira_results.json", [result.to_dict() for result in jira_results])
    write_json(output / "slack_payload.json", slack_payload)
    write_json(output / "automation_result.json", {
        "analysis": analysis.to_dict(),
        "jira_results": [result.to_dict() for result in jira_results],
        "slack_result": slack_result.to_dict(),
    })
    write_run_report(
        output_dir=output,
        settings=settings,
        meeting_title=meeting_title,
        analysis=analysis,
        jira_results=jira_results,
        slack_result=slack_result,
        mode=mode,
    )

    return AutomationResult(
        analysis=analysis,
        jira_results=jira_results,
        slack_result=slack_result,
        output_dir=str(output),
    )
