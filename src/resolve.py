#!/usr/bin/env python3
"""Resolve extracted meeting items before Jira ticket creation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SETTINGS = {
    "confidence_threshold": 0.5,
    "meeting_date": "2026-06-16",
    "meeting_id": "transcript.txt",
    "jira_project_key": None,
    "default_priority": "Medium",
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

VAGUE_DATE_WORDS = (
    "this month",
    "sometime",
    "some time",
    "soon",
    "later",
    "eventually",
    "next few weeks",
    "next couple weeks",
)

GENERIC_OWNER_PLACEHOLDERS = {
    "someone",
    "somebody",
    "anyone",
    "anybody",
    "everyone",
    "no one",
    "nobody",
    "tbd",
    "to be decided",
    "to be assigned",
    "unassigned",
    "unknown",
    "n/a",
    "na",
}


def load_settings(path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    """Load settings.yaml with safe defaults."""
    settings = dict(DEFAULT_SETTINGS)
    settings_path = Path(path)

    if settings_path.exists():
        loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Settings file must be a YAML object: {settings_path}")
        settings.update(loaded)

    # Support env override for Jira project key as requested.
    if os.getenv("JIRA_PROJECT_KEY"):
        settings["jira_project_key"] = os.getenv("JIRA_PROJECT_KEY")

    # Support uppercase aliases in case config uses env-style names.
    if "MEETING_DATE" in settings and settings["MEETING_DATE"]:
        settings["meeting_date"] = settings["MEETING_DATE"]
    if "MEETING_ID" in settings and settings["MEETING_ID"]:
        settings["meeting_id"] = settings["MEETING_ID"]

    return settings


def load_owners(path: str | Path = "config/owners.json") -> dict[str, str]:
    """Load owners.json as case-insensitive owner -> Jira account ID map."""
    owners_path = Path(path)
    if not owners_path.exists():
        return {}

    raw = json.loads(owners_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Owners file must be a JSON object: {owners_path}")

    owners: dict[str, str] = {}
    for name, value in raw.items():
        account_id = _extract_account_id(value)
        owners[str(name).strip().lower()] = account_id
    return owners


def _extract_account_id(value: Any) -> str:
    """Support owners.json values as strings or objects."""
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        for key in ("jira_account_id", "account_id", "accountId"):
            account_id = value.get(key)
            if account_id:
                return str(account_id).strip()

    return ""


def resolve_items(items: list[dict]) -> list[dict]:
    """Resolve owners, dates, flags, and idempotency keys for extracted items."""
    settings = load_settings()
    owners = load_owners()

    threshold = float(settings.get("confidence_threshold", 0.5))
    meeting_date = _parse_iso_date(str(settings.get("meeting_date") or DEFAULT_SETTINGS["meeting_date"]))
    meeting_id = str(settings.get("meeting_id") or DEFAULT_SETTINGS["meeting_id"])
    default_priority = str(settings.get("default_priority") or "Medium")

    resolved_items: list[dict] = []
    for item in items:
        resolved = deepcopy(item)

        priority = resolved.get("priority") or default_priority
        resolved["priority"] = priority if priority in {"High", "Medium", "Low"} else default_priority

        assignee_account_id, owner_status = _resolve_owner(resolved, owners)
        resolved["assignee_account_id"] = assignee_account_id
        resolved["owner_status"] = owner_status

        resolved["due_date"] = normalize_due_date(resolved.get("due_date_raw"), meeting_date)

        confidence = float(resolved.get("confidence") or 0.0)
        item_type = resolved.get("type")
        resolved["should_create_ticket"] = item_type == "action_item" and confidence >= threshold
        resolved["needs_human_review"] = (
            owner_status == "needs_review"
            or (owner_status == "unassigned" and resolved["priority"] == "High")
            or confidence < threshold
        )

        task = str(resolved.get("task") or "")
        resolved["idempotency_key"] = make_idempotency_key(meeting_id, task)
        resolved_items.append(resolved)

    return resolved_items


def _resolve_owner(item: dict[str, Any], owners: dict[str, str]) -> tuple[str | None, str]:
    """Resolve owner_raw to Jira account ID and status."""
    owner_raw = item.get("owner_raw")

    if owner_raw is None or not str(owner_raw).strip():
        # Extraction can leave owner_raw null when transcript names an ambiguous person
        # in context (example: "which Ravi?"). Flag those for human review.
        if _mentions_unmapped_person(item, owners):
            return None, "needs_review"
        return None, "unassigned"

    owner_key = str(owner_raw).strip().lower()
    if owner_key in GENERIC_OWNER_PLACEHOLDERS:
        return None, "unassigned"

    account_id = owners.get(owner_key)
    if account_id:
        return account_id, "resolved"

    return None, "needs_review"


def _mentions_unmapped_person(item: dict[str, Any], owners: dict[str, str]) -> bool:
    """Detect ambiguous named owners mentioned outside owner_raw."""
    text = f"{item.get('task') or ''} {item.get('source_quote') or ''}"
    candidates = set(re.findall(r"\b[A-Z][a-z]+\b", text))
    false_positives = {
        "I",
        "We",
        "Can",
        "Which",
        "Oh",
        "Yeah",
        "Okay",
        "Let",
        "Someone",
        "Somebody",
        "Anyone",
        "Anybody",
        "High",
        "Medium",
        "Low",
    }

    for candidate in candidates:
        key = candidate.lower()
        if candidate in false_positives or key in owners or key in GENERIC_OWNER_PLACEHOLDERS:
            continue
        if re.search(rf"\bwhich\s+{re.escape(candidate)}\b", text, re.I) or re.search(
            rf"\btwo\s+{re.escape(candidate)}s\b", text, re.I
        ):
            return True

    return False


def normalize_due_date(due_date_raw: Any, meeting_date: date) -> str | None:
    """Convert spoken due dates to ISO date strings."""
    if due_date_raw is None or not str(due_date_raw).strip():
        return None

    raw = str(due_date_raw).strip()
    text = raw.lower().strip(" .,!?")

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)

    if any(word in text for word in VAGUE_DATE_WORDS):
        return None

    if "today" in text:
        return meeting_date.isoformat()

    if "tomorrow" in text:
        return (meeting_date + timedelta(days=1)).isoformat()

    for weekday_name, weekday_number in WEEKDAYS.items():
        if re.search(rf"\b{weekday_name}\b", text):
            days_ahead = (weekday_number - meeting_date.weekday()) % 7
            return (meeting_date + timedelta(days=days_ahead)).isoformat()

    return None


def make_idempotency_key(meeting_id: str, task: str) -> str:
    """Hash meeting ID plus normalized task text."""
    normalized_task = " ".join(task.lower().split())
    raw = f"{meeting_id}:{normalized_task}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _parse_iso_date(value: str) -> date:
    """Parse YYYY-MM-DD date."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"meeting_date must be YYYY-MM-DD, got {value!r}") from exc


def _load_items(path: Path) -> list[dict]:
    """Load extract.py action_items.json shape."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    raise ValueError('Input must be a JSON object with an "items" list, or a list of items.')


def _print_summary(items: list[dict]) -> None:
    """Print compact summary table."""
    headers = ["task", "type", "owner_status", "assignee", "due_date", "create?", "review?"]
    widths = [42, 12, 14, 18, 10, 7, 7]
    print(_format_row(headers, widths))
    print(_format_row(["-" * width for width in widths], widths))

    for item in items:
        row = [
            _shorten(str(item.get("task") or ""), widths[0]),
            str(item.get("type") or ""),
            str(item.get("owner_status") or ""),
            _shorten(str(item.get("assignee_account_id") or ""), widths[3]),
            str(item.get("due_date") or ""),
            "yes" if item.get("should_create_ticket") else "no",
            "yes" if item.get("needs_human_review") else "no",
        ]
        print(_format_row(row, widths))


def _format_row(values: list[str], widths: list[int]) -> str:
    return " | ".join(str(value).ljust(width)[:width] for value, width in zip(values, widths))


def _shorten(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(0, width - 1)] + "…"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve extracted items before Jira creation.")
    parser.add_argument("--in", dest="input", default="data/action_items.json", help="Input action_items JSON.")
    parser.add_argument("--out", default="data/resolved_items.json", help="Output resolved_items JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    out_path = Path(args.out)

    if not input_path.exists() or not input_path.is_file():
        print(f"Error: input file not found: {input_path}")
        return 1

    try:
        items = _load_items(input_path)
        resolved = resolve_items(items)
    except Exception as exc:  # noqa: BLE001 - CLI should show readable failures.
        print(f"Error: resolution failed: {exc}")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"items": resolved}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    _print_summary(resolved)
    print(f"\nWrote: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
