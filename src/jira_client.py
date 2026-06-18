#!/usr/bin/env python3
"""Create Jira issues from resolved meeting items."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

JIRA_ISSUE_ENDPOINT = "/rest/api/3/issue"
STATE_DB_PATH = Path("data/state.db")
AUDIT_LOG_PATH = Path("data/audit_log.json")


class JiraConfigError(RuntimeError):
    """Missing or invalid Jira configuration."""


class JiraAPIError(RuntimeError):
    """Non-retryable Jira API failure."""


class RetryableJiraError(RuntimeError):
    """Retryable Jira API failure."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise JiraConfigError(f"{name} is not set. Add it to .env.")
    return value


def _jira_config(dry_run: bool = False) -> dict[str, str]:
    """Load Jira config and auth headers from .env."""
    load_dotenv()

    if dry_run:
        base_url = os.getenv("JIRA_BASE_URL", "https://example.atlassian.net").strip().rstrip("/")
        email = os.getenv("JIRA_EMAIL", "dry-run@example.com").strip()
        api_token = os.getenv("JIRA_API_TOKEN", "dry-run-token").strip()
        project_key = os.getenv("JIRA_PROJECT_KEY", "DRYRUN").strip() or "DRYRUN"
    else:
        base_url = _required_env("JIRA_BASE_URL").rstrip("/")
        email = _required_env("JIRA_EMAIL")
        api_token = _required_env("JIRA_API_TOKEN")
        project_key = _required_env("JIRA_PROJECT_KEY")

    encoded = base64.b64encode(f"{email}:{api_token}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    return {
        "base_url": base_url,
        "project_key": project_key,
        "headers": headers,
    }


def _init_state_db(db_path: Path = STATE_DB_PATH) -> sqlite3.Connection:
    """Open SQLite store and create idempotency table."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS created_tickets (
            idempotency_key TEXT PRIMARY KEY,
            issue_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _lookup_existing_ticket(conn: sqlite3.Connection, idempotency_key: str) -> str | None:
    row = conn.execute(
        "SELECT issue_key FROM created_tickets WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    return str(row[0]) if row else None


def _record_created_ticket(conn: sqlite3.Connection, idempotency_key: str, issue_key: str) -> None:
    conn.execute(
        "INSERT INTO created_tickets (idempotency_key, issue_key, created_at) VALUES (?, ?, ?)",
        (idempotency_key, issue_key, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _adf_paragraph(text: str) -> dict[str, Any]:
    """Build one ADF paragraph."""
    return {
        "type": "paragraph",
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }


def _review_reason(item: dict[str, Any]) -> str:
    """Explain why item needs human review."""
    reasons: list[str] = []
    owner_status = item.get("owner_status")

    if owner_status == "needs_review":
        owner_raw = item.get("owner_raw") or "unknown/ambiguous owner"
        reasons.append(f"ambiguous or unmapped owner: {owner_raw}")

    if owner_status == "unassigned" and item.get("priority") == "High":
        reasons.append("unassigned high-priority item")

    confidence = item.get("confidence")
    try:
        if confidence is not None and float(confidence) < 0.5:
            reasons.append(f"low confidence: {float(confidence):.2f}")
    except (TypeError, ValueError):
        reasons.append("invalid confidence value")

    return "; ".join(reasons) or "human review requested"


def build_issue_payload(item: dict[str, Any], project_key: str) -> dict[str, Any]:
    """Build Jira issue create payload."""
    summary = str(item.get("task") or "Untitled task").strip()[:255]
    owner_raw = item.get("owner_raw") or "unassigned"
    due_date = item.get("due_date")

    paragraphs = [
        _adf_paragraph(f"Source quote: {item.get('source_quote') or 'Not provided'}"),
        _adf_paragraph(f"Priority: {item.get('priority') or 'Medium'}"),
        _adf_paragraph(f"Original spoken owner: {owner_raw}"),
    ]

    if due_date:
        paragraphs.append(_adf_paragraph(f"Due date: {due_date}"))

    if item.get("needs_human_review"):
        paragraphs.append(_adf_paragraph(f"NEEDS REVIEW: {_review_reason(item)}"))

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": "Task"},
        "description": {
            "type": "doc",
            "version": 1,
            "content": paragraphs,
        },
    }

    assignee_account_id = item.get("assignee_account_id")
    if assignee_account_id:
        fields["assignee"] = {"id": assignee_account_id}

    if due_date:
        fields["duedate"] = due_date

    return {"fields": fields}


@retry(
    retry=retry_if_exception_type((requests.RequestException, RetryableJiraError)),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _post_issue(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """POST issue to Jira with retry for connection errors and 429/5xx."""
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)

    if response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableJiraError(f"Jira API returned retryable status {response.status_code}: {response.text}")

    if response.status_code < 200 or response.status_code >= 300:
        raise JiraAPIError(f"Jira API returned status {response.status_code}: {response.text}")

    try:
        data = response.json()
    except ValueError as exc:
        raise JiraAPIError(f"Jira API returned invalid JSON: {response.text}") from exc

    if not data.get("key"):
        raise JiraAPIError(f"Jira API response did not include issue key: {data}")

    return data


def create_tickets(resolved_items: list[dict], dry_run: bool = False) -> dict[str, Any]:
    """Create Jira tickets from resolved items."""
    config = _jira_config(dry_run=dry_run)
    url = f"{config['base_url']}{JIRA_ISSUE_ENDPOINT}"
    project_key = config["project_key"]
    headers = config["headers"]

    results: dict[str, Any] = {
        "created": 0,
        "skipped": 0,
        "failed": 0,
        "created_issue_keys": [],
        "errors": [],
        "items": [],
    }

    conn: sqlite3.Connection | None = None
    if not dry_run:
        conn = _init_state_db()

    try:
        for item in resolved_items:
            task = str(item.get("task") or "Untitled task")
            item_result = {
                "task": task,
                "status": "",
                "issue_key": None,
                "reason": None,
                "payload": None,
            }

            if not item.get("should_create_ticket"):
                item_result["status"] = "skipped"
                item_result["reason"] = "should_create_ticket is false"
                results["skipped"] += 1
                results["items"].append(item_result)
                continue

            idempotency_key = str(item.get("idempotency_key") or "")
            if not idempotency_key:
                item_result["status"] = "failed"
                item_result["reason"] = "missing idempotency_key"
                results["failed"] += 1
                results["errors"].append({"task": task, "error": item_result["reason"]})
                results["items"].append(item_result)
                continue

            try:
                if conn is not None:
                    existing_issue_key = _lookup_existing_ticket(conn, idempotency_key)
                    if existing_issue_key:
                        print(f"already exists: {existing_issue_key}")
                        item_result["status"] = "skipped"
                        item_result["issue_key"] = existing_issue_key
                        item_result["reason"] = "exists"
                        results["skipped"] += 1
                        results["items"].append(item_result)
                        continue

                payload = build_issue_payload(item, project_key)
                item_result["payload"] = payload

                if dry_run:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                    item_result["status"] = "skipped"
                    item_result["reason"] = "dry-run"
                    results["skipped"] += 1
                    results["items"].append(item_result)
                    continue

                response_data = _post_issue(url, headers, payload)
                issue_key = str(response_data["key"])
                _record_created_ticket(conn, idempotency_key, issue_key)

                item_result["status"] = "created"
                item_result["issue_key"] = issue_key
                results["created"] += 1
                results["created_issue_keys"].append(issue_key)
                results["items"].append(item_result)

            except Exception as exc:  # noqa: BLE001 - partial failure per item.
                item_result["status"] = "failed"
                item_result["reason"] = str(exc)
                results["failed"] += 1
                results["errors"].append({"task": task, "error": str(exc)})
                results["items"].append(item_result)
                continue

    finally:
        if conn is not None:
            conn.close()

    return results


def _load_resolved_items(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    raise ValueError('Input must be a JSON object with an "items" list, or a list of items.')


def _append_audit_log(results: dict[str, Any], dry_run: bool) -> None:
    """Append audit summary using src/audit.py if present, else data/audit_log.json."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "jira_create_tickets",
        "dry_run": dry_run,
        "results": results,
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


def _print_summary(results: dict[str, Any]) -> None:
    for item in results.get("items", []):
        task = item.get("task") or "Untitled task"
        status = item.get("status")
        if status == "created":
            print(f"{task} -> CREATED {item.get('issue_key')}")
        elif status == "skipped" and item.get("reason") == "exists":
            print(f"{task} -> SKIPPED (exists: {item.get('issue_key')})")
        elif status == "skipped":
            print(f"{task} -> SKIPPED ({item.get('reason')})")
        else:
            print(f"{task} -> FAILED {item.get('reason')}")

    print("\nTotals:")
    print(f"created: {results['created']}")
    print(f"skipped: {results['skipped']}")
    print(f"failed: {results['failed']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Jira issues from resolved_items.json.")
    parser.add_argument("--in", dest="input", default="data/resolved_items.json", help="Resolved items JSON file.")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without POSTing or writing SQLite.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input)

    if not input_path.exists() or not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        resolved_items = _load_resolved_items(input_path)
        results = create_tickets(resolved_items, dry_run=args.dry_run)
        _print_summary(results)
        _append_audit_log(results, args.dry_run)
    except Exception as exc:  # noqa: BLE001 - CLI should show readable failures.
        print(f"Error: Jira ticket creation failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
