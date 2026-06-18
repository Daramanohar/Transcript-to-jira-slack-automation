#!/usr/bin/env python3
"""Post Jira ticket summary to Slack via chat.postMessage."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
STATE_DB_PATH = Path("data/state.db")
AUDIT_LOG_PATH = Path("data/audit_log.json")
OWNERS_PATH = Path("config/owners.json")
SETTINGS_PATH = Path("config/settings.yaml")


class SlackConfigError(RuntimeError):
    """Missing Slack/Jira config."""


class SlackAPIError(RuntimeError):
    """Non-retryable Slack API error."""


class RetryableSlackError(RuntimeError):
    """Retryable Slack API error."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SlackConfigError(f"{name} is not set. Add it to .env.")
    return value


def _slack_config(dry_run: bool = False) -> dict[str, str]:
    """Load Slack/Jira config from .env."""
    load_dotenv()

    jira_base_url = os.getenv("JIRA_BASE_URL", "").strip().rstrip("/")
    if not jira_base_url:
        if dry_run:
            jira_base_url = "https://example.atlassian.net"
        else:
            raise SlackConfigError("JIRA_BASE_URL is not set. Add it to .env.")

    if dry_run:
        token = os.getenv("SLACK_BOT_TOKEN", "dry-run-token")
        channel = os.getenv("SLACK_CHANNEL", "dry-run-channel")
    else:
        token = _required_env("SLACK_BOT_TOKEN")
        channel = _required_env("SLACK_CHANNEL")

    return {
        "token": token,
        "channel": channel,
        "jira_base_url": jira_base_url,
    }


def _load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    loaded = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _load_resolved_items(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    raise ValueError('Input must be a JSON object with an "items" list, or a list of items.')


def _load_owner_display_names(path: Path = OWNERS_PATH) -> tuple[dict[str, str], dict[str, str]]:
    """Return owner-name and account-id lookup maps to display names."""
    if not path.exists():
        return {}, {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}, {}

    by_name: dict[str, str] = {}
    by_account_id: dict[str, str] = {}

    for name, value in raw.items():
        display_name = str(name)
        account_id = ""
        if isinstance(value, dict):
            display_name = str(value.get("display_name") or value.get("name") or name)
            account_id = str(value.get("jira_account_id") or value.get("account_id") or value.get("accountId") or "")
        elif isinstance(value, str):
            account_id = value

        by_name[str(name).strip().lower()] = display_name
        if account_id:
            by_account_id[account_id.strip()] = display_name

    return by_name, by_account_id


def _owner_display_name(item: dict[str, Any], by_name: dict[str, str], by_account_id: dict[str, str]) -> str:
    account_id = item.get("assignee_account_id")
    if account_id and str(account_id) in by_account_id:
        return by_account_id[str(account_id)]

    owner_raw = item.get("owner_raw")
    if owner_raw and str(owner_raw).strip().lower() in by_name:
        return by_name[str(owner_raw).strip().lower()]

    return "Unassigned"


def _issue_key_map(create_result: dict[str, Any]) -> dict[str, str]:
    """Map task text to Jira issue key from create_tickets result."""
    mapping: dict[str, str] = {}
    for item in create_result.get("items", []):
        task = str(item.get("task") or "")
        issue_key = item.get("issue_key")
        if task and issue_key:
            mapping[task] = str(issue_key)
    return mapping


def _jira_link(jira_base_url: str, issue_key: str | None) -> str:
    if not issue_key:
        return "NO KEY"
    url = f"{jira_base_url}/browse/{issue_key}"
    return f"<{url}|{issue_key}>"


def _review_reason(item: dict[str, Any]) -> str:
    reasons: list[str] = []
    owner_status = item.get("owner_status")

    if owner_status == "needs_review":
        owner_raw = item.get("owner_raw") or "unknown/ambiguous owner"
        reasons.append(f"ambiguous owner: {owner_raw}")

    if owner_status == "unassigned" and item.get("priority") == "High":
        reasons.append("unassigned high-priority item")

    return "; ".join(reasons) or "needs human review"


def _section_lines(title: str, lines: list[str]) -> list[dict[str, Any]]:
    """Build Slack section blocks, chunking long line groups."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}}
    ]

    if not lines:
        lines = ["—"]

    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > 2800:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
            chunk = line
        else:
            chunk = candidate

    if chunk:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    return blocks


def build_blocks(create_result: dict[str, Any], resolved_items: list[dict], jira_base_url: str) -> list[dict[str, Any]]:
    """Build Block Kit summary message."""
    settings = _load_settings()
    meeting_label = str(settings.get("meeting_id") or settings.get("meeting_date") or datetime.now().date().isoformat())
    issue_by_task = _issue_key_map(create_result)
    by_name, by_account_id = _load_owner_display_names()

    created_items = [
        item for item in resolved_items if item.get("should_create_ticket") and issue_by_task.get(str(item.get("task") or ""))
    ]
    needs_review_items = [item for item in created_items if item.get("needs_human_review")]
    excluded_items = [item for item in resolved_items if item.get("type") in {"decision", "idea", "cancelled"}]

    created_lines = []
    for item in created_items:
        task = str(item.get("task") or "Untitled task")
        issue_key = issue_by_task.get(task)
        owner = _owner_display_name(item, by_name, by_account_id)
        due = item.get("due_date") or "—"
        created_lines.append(f"• {_jira_link(jira_base_url, issue_key)} — {task}  ·  owner: {owner}  ·  due: {due}")

    review_lines = []
    for item in needs_review_items:
        task = str(item.get("task") or "Untitled task")
        issue_key = issue_by_task.get(task)
        review_lines.append(f"• {_jira_link(jira_base_url, issue_key)} — {task}  ·  {_review_reason(item)}")

    excluded_lines = []
    for item in excluded_items:
        item_type = item.get("type") or "unknown"
        task = item.get("task") or "Untitled item"
        excluded_lines.append(f"• `{item_type}` — {task}")

    context = f"{len(created_items)} tickets created · {len(needs_review_items)} need review · {len(excluded_items)} items excluded"

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Meeting Action Items — {meeting_label}", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
        {"type": "divider"},
    ]
    blocks.extend(_section_lines("✅ Created tickets", created_lines))
    blocks.append({"type": "divider"})
    blocks.extend(_section_lines("⚠️ Needs review", review_lines))
    blocks.append({"type": "divider"})
    blocks.extend(_section_lines("🚫 Not ticketed (for awareness)", excluded_lines))
    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Generated automatically from the meeting transcript."}],
        }
    )
    return blocks


@retry(
    retry=retry_if_exception_type((requests.RequestException, RetryableSlackError)),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _post_message(token: str, channel: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Post Slack message with retry for connection errors and 429/5xx."""
    payload = {
        "channel": channel,
        "text": "Meeting Action Items",
        "blocks": blocks,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(SLACK_POST_MESSAGE_URL, headers=headers, data=json.dumps(payload), timeout=30)

    if response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableSlackError(f"Slack API returned retryable status {response.status_code}: {response.text}")

    if response.status_code < 200 or response.status_code >= 300:
        raise SlackAPIError(f"Slack API returned status {response.status_code}: {response.text}")

    try:
        data = response.json()
    except ValueError as exc:
        raise SlackAPIError(f"Slack API returned invalid JSON: {response.text}") from exc

    if not data.get("ok"):
        raise SlackAPIError(f"Slack API error: {data.get('error', 'unknown_error')}")

    return data


def post_summary(create_result: dict[str, Any], resolved_items: list[dict], dry_run: bool = False) -> dict[str, Any]:
    """Post or print Slack Block Kit summary."""
    config = _slack_config(dry_run=dry_run)
    blocks = build_blocks(create_result, resolved_items, config["jira_base_url"])

    if dry_run:
        print(json.dumps({"channel": config["channel"], "text": "Meeting Action Items", "blocks": blocks}, indent=2, ensure_ascii=True))
        return {
            "ok": True,
            "dry_run": True,
            "channel": config["channel"],
            "blocks": blocks,
        }

    response = _post_message(config["token"], config["channel"], blocks)
    return {
        "ok": True,
        "dry_run": False,
        "channel": config["channel"],
        "ts": response.get("ts"),
        "response": response,
    }


def _derive_create_result_from_state(resolved_items: list[dict]) -> dict[str, Any]:
    """Build create_tickets-like result from local SQLite idempotency store."""
    result: dict[str, Any] = {
        "created": 0,
        "skipped": 0,
        "failed": 0,
        "created_issue_keys": [],
        "errors": [],
        "items": [],
    }

    conn: sqlite3.Connection | None = None
    if STATE_DB_PATH.exists():
        conn = sqlite3.connect(STATE_DB_PATH)

    try:
        for item in resolved_items:
            task = str(item.get("task") or "Untitled task")
            row = None
            if conn is not None and item.get("idempotency_key"):
                row = conn.execute(
                    "SELECT issue_key FROM created_tickets WHERE idempotency_key = ?",
                    (str(item.get("idempotency_key")),),
                ).fetchone()

            if item.get("should_create_ticket") and row:
                issue_key = str(row[0])
                result["created"] += 1
                result["created_issue_keys"].append(issue_key)
                result["items"].append({"task": task, "status": "created", "issue_key": issue_key, "reason": "state"})
            elif item.get("should_create_ticket"):
                result["failed"] += 1
                result["errors"].append({"task": task, "error": "not found in SQLite state store"})
                result["items"].append({"task": task, "status": "failed", "issue_key": None, "reason": "not found in state"})
            else:
                result["skipped"] += 1
                result["items"].append(
                    {"task": task, "status": "skipped", "issue_key": None, "reason": "should_create_ticket is false"}
                )
    finally:
        if conn is not None:
            conn.close()

    return result


def _append_audit_log(outcome: dict[str, Any]) -> None:
    """Append audit outcome using src/audit.py if present, else data/audit_log.json."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "slack_post_summary",
        "outcome": outcome,
    }

    try:
        import audit  # type: ignore[import-not-found]

        if hasattr(audit, "append"):
            audit.append(record)  # type: ignore[attr-defined]
            return
        if hasattr(audit, "append_audit_log"):
            audit.append_audit_log(record)  # type: ignore[attr-defined]
            return
    except Exception:
        pass

    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if AUDIT_LOG_PATH.exists():
        try:
            existing = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
        except ValueError:
            existing = []
    else:
        existing = []

    if not isinstance(existing, list):
        existing = [existing]
    existing.append(record)
    AUDIT_LOG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Post meeting action item summary to Slack.")
    parser.add_argument("--in", dest="input", default="data/resolved_items.json", help="Resolved items JSON file.")
    parser.add_argument("--dry-run", action="store_true", help="Print Block Kit JSON instead of posting.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input)

    if not input_path.exists() or not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        resolved_items = _load_resolved_items(input_path)
        create_result = _derive_create_result_from_state(resolved_items)
        outcome = post_summary(create_result, resolved_items, dry_run=args.dry_run)
        _append_audit_log(outcome)
        print("Slack summary dry-run complete." if args.dry_run else "Slack summary posted.")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should show readable failure.
        outcome = {"ok": False, "dry_run": args.dry_run, "error": str(exc)}
        try:
            _append_audit_log(outcome)
        except Exception:
            pass
        print(f"Error: Slack summary failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
