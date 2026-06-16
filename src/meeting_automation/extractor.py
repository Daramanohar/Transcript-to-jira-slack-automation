from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from typing import Any

from .config import Settings
from .models import ActionItem, MeetingAnalysis
from .util import slugify_label

ACTION_KEYWORDS = (
    "action",
    "will",
    "needs to",
    "need to",
    "follow up",
    "create",
    "draft",
    "schedule",
    "confirm",
    "review",
    "add",
    "send",
    "prepare",
)


def extract_meeting_analysis(
    transcript: str,
    settings: Settings,
    meeting_title: str,
    meeting_date: str | None = None,
) -> MeetingAnalysis:
    if settings.openai_api_key and os.getenv("DISABLE_OPENAI_EXTRACTION", "").lower() not in {"1", "true", "yes"}:
        try:
            return _extract_with_openai(transcript, settings, meeting_title, meeting_date)
        except Exception as exc:
            fallback = _extract_with_rules(transcript)
            fallback.summary = (
                f"{fallback.summary} OpenAI extraction failed, so deterministic fallback was used. "
                f"Failure: {exc}"
            )
            return fallback

    return _extract_with_rules(transcript)


def _extract_with_openai(
    transcript: str,
    settings: Settings,
    meeting_title: str,
    meeting_date: str | None,
) -> MeetingAnalysis:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "decisions", "risks", "action_items"],
        "properties": {
            "summary": {"type": "string"},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "title",
                        "description",
                        "owner",
                        "due_date",
                        "priority",
                        "labels",
                        "source_quote",
                        "confidence",
                    ],
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "owner": {"type": ["string", "null"]},
                        "due_date": {"type": ["string", "null"]},
                        "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "source_quote": {"type": ["string", "null"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
        },
    }

    prompt = f"""
You are converting meeting notes into workflow automation data.

Meeting title: {meeting_title}
Meeting date: {meeting_date or date.today().isoformat()}

Rules:
- Extract only concrete action items, not vague ideas.
- Keep titles short enough for Jira issue summaries.
- If owner or due date is missing, use null and explain the ambiguity in the description.
- Resolve explicit dates to ISO format when possible.
- Priority should be High only for blockers, deadlines, or patient/clinical risk.
- Labels should be Jira-safe lowercase tags.
- Include a short source_quote from the notes for traceability.

Transcript or notes:
{transcript}
""".strip()

    body = {
        "model": settings.openai_model,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "meeting_analysis",
                "strict": True,
                "schema": schema,
            }
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API returned {exc.code}: {message}") from exc

    output_text = _extract_response_text(data)
    parsed = json.loads(output_text)
    return _analysis_from_payload(parsed, "openai_structured_outputs")


def _extract_response_text(data: dict[str, Any]) -> str:
    if "output_text" in data:
        return str(data["output_text"])

    fragments: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and "text" in content:
                fragments.append(str(content["text"]))

    if not fragments:
        raise RuntimeError("OpenAI response did not include output text.")
    return "".join(fragments)


def _analysis_from_payload(payload: dict[str, Any], method: str) -> MeetingAnalysis:
    return MeetingAnalysis(
        summary=str(payload.get("summary", "")).strip(),
        decisions=[str(item).strip() for item in payload.get("decisions", []) if str(item).strip()],
        risks=[str(item).strip() for item in payload.get("risks", []) if str(item).strip()],
        action_items=[
            ActionItem(
                title=str(item.get("title", "")).strip()[:180],
                description=str(item.get("description", "")).strip(),
                owner=item.get("owner"),
                due_date=item.get("due_date"),
                priority=item.get("priority", "Medium"),
                labels=[slugify_label(str(label)) for label in item.get("labels", []) if str(label).strip()],
                source_quote=item.get("source_quote"),
                confidence=float(item.get("confidence", 0.75)),
            )
            for item in payload.get("action_items", [])
            if str(item.get("title", "")).strip()
        ],
        extraction_method=method,
    )


def _extract_with_rules(transcript: str) -> MeetingAnalysis:
    lines = [line.strip(" -\t") for line in transcript.splitlines() if line.strip()]
    decisions = _collect_section_items(lines, "decisions")
    risks = _collect_section_items(lines, "risks")
    action_lines = _collect_section_items(lines, "action items")

    if not action_lines:
        action_lines = [
            line
            for line in lines
            if any(keyword in line.lower() for keyword in ACTION_KEYWORDS)
            and len(line.split()) >= 4
        ]

    action_items = [_action_from_line(line) for line in action_lines]
    action_items = _dedupe_actions(action_items)

    summary = (
        f"Identified {len(action_items)} action item(s), "
        f"{len(decisions)} decision(s), and {len(risks)} risk(s) from the meeting notes."
    )

    return MeetingAnalysis(
        summary=summary,
        decisions=decisions,
        risks=risks,
        action_items=action_items,
        extraction_method="deterministic_fallback",
    )


def _collect_section_items(lines: list[str], section_name: str) -> list[str]:
    collected: list[str] = []
    in_section = False
    section_key = section_name.lower().rstrip(":")

    for line in lines:
        normalized = line.lower().rstrip(":")
        is_heading = normalized in {
            "context",
            "discussion",
            "decisions",
            "decision",
            "action items",
            "actions",
            "risks",
            "risk",
            "participants",
        }

        if normalized == section_key or (
            section_key == "action items" and normalized in {"actions", "action items"}
        ):
            in_section = True
            continue

        if in_section and is_heading:
            break

        if in_section:
            collected.append(line)

    return collected


def _action_from_line(line: str) -> ActionItem:
    owner = _guess_owner(line)
    due_date = _guess_due_date(line)
    priority = (
        "High"
        if re.search(r"\b(blocker|urgent|deadline|patient safety|clinical risk|at risk)\b", line, re.I)
        else "Medium"
    )

    cleaned = re.sub(r"^action\s*[:\-]\s*", "", line, flags=re.I).strip()
    title = _title_from_action(cleaned, owner)
    labels = [slugify_label("meeting-action")]
    if owner:
        labels.append(slugify_label(owner))

    return ActionItem(
        title=title,
        description=(
            f"Action extracted from meeting notes.\n\nSource: {line}\n\n"
            f"Owner: {owner or 'Not specified'}\n"
            f"Due date: {due_date or 'Not specified'}"
        ),
        owner=owner,
        due_date=due_date,
        priority=priority,
        labels=labels,
        source_quote=line,
        confidence=0.7,
    )


def _guess_owner(line: str) -> str | None:
    patterns = [
        r"^([A-Z][a-zA-Z]+)\s+will\b",
        r"^([A-Z][a-zA-Z]+)\s+needs?\s+to\b",
        r"^([A-Z][a-zA-Z]+)\s+to\b",
        r"owner\s*[:\-]\s*([A-Z][a-zA-Z]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1)
    return None


def _guess_due_date(line: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", line)
    if iso_match:
        return iso_match.group(1)

    natural_match = re.search(r"\b(by|before|on)\s+([A-Z][a-z]+\s+\d{1,2}|next\s+\w+|tomorrow)\b", line, re.I)
    if natural_match:
        return natural_match.group(2)

    loose_relative_match = re.search(r"\b(next\s+week|next\s+\w+|tomorrow)\b", line, re.I)
    if loose_relative_match:
        return loose_relative_match.group(1)

    return None


def _title_from_action(line: str, owner: str | None) -> str:
    title = re.sub(r"\s+by\s+20\d{2}-\d{2}-\d{2}\.?$", "", line, flags=re.I)
    if owner:
        title = re.sub(rf"^{re.escape(owner)}\s+will\s+", "", title, flags=re.I)
    title = title.replace("`", "").strip(". ")
    if not title:
        title = line.strip(". ")
    return title[:180]


def _dedupe_actions(items: list[ActionItem]) -> list[ActionItem]:
    seen: set[str] = set()
    unique: list[ActionItem] = []
    for item in items:
        key = item.title.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
