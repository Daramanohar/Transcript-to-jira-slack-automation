from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .config import Settings
from .models import ActionItem, JiraTicketResult


class JiraClient:
    def __init__(self, settings: Settings, dry_run: bool) -> None:
        self.settings = settings
        self.dry_run = dry_run or not settings.jira_ready

    def build_issue_payload(self, action: ActionItem) -> dict:
        labels = sorted(set(action.labels + ["automated-meeting-action"]))
        description = _jira_adf_description(action)
        fields = {
            "project": {"key": self.settings.jira_project_key or "DRY"},
            "summary": action.title,
            "description": description,
            "issuetype": {"name": self.settings.jira_issue_type},
            "labels": labels,
        }
        return {"fields": fields}

    def create_many(self, actions: list[ActionItem]) -> tuple[list[JiraTicketResult], list[dict]]:
        results: list[JiraTicketResult] = []
        payloads: list[dict] = []

        for index, action in enumerate(actions, start=1):
            payload = self.build_issue_payload(action)
            payloads.append(payload)

            if self.dry_run:
                key = f"DRY-{index:03d}"
                results.append(
                    JiraTicketResult(
                        action_title=action.title,
                        created=True,
                        key=key,
                        url=f"dry-run://jira/{key}",
                    )
                )
                continue

            results.append(self._create_issue(action, payload))

        return results, payloads

    def _create_issue(self, action: ActionItem, payload: dict) -> JiraTicketResult:
        assert self.settings.jira_base_url is not None
        url = f"{self.settings.jira_base_url}/rest/api/3/issue"
        token = base64.b64encode(
            f"{self.settings.jira_email}:{self.settings.jira_api_token}".encode("utf-8")
        ).decode("ascii")

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            return JiraTicketResult(
                action_title=action.title,
                created=False,
                key=None,
                url=None,
                error=f"Jira API returned {exc.code}: {message}",
            )
        except Exception as exc:
            return JiraTicketResult(
                action_title=action.title,
                created=False,
                key=None,
                url=None,
                error=str(exc),
            )

        key = data.get("key")
        return JiraTicketResult(
            action_title=action.title,
            created=True,
            key=key,
            url=f"{self.settings.jira_base_url}/browse/{key}" if key else None,
        )


def _jira_adf_description(action: ActionItem) -> dict:
    lines = [
        action.description,
        "",
        f"Owner: {action.owner or 'Not specified'}",
        f"Due date: {action.due_date or 'Not specified'}",
        f"Priority: {action.priority}",
        f"Confidence: {action.confidence:.2f}",
    ]
    if action.source_quote:
        lines.extend(["", f"Source quote: {action.source_quote}"])

    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line or " "}],
            }
            for line in lines
        ],
    }
