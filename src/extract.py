"""Extract structured meeting items from a transcript using an LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from .llm_client import get_llm_client
else:
    from llm_client import get_llm_client


EXTRACTION_INSTRUCTIONS = """You extract structured items from a meeting transcript for an
operations team. Read the whole transcript and return ONLY valid JSON,
no markdown, no commentary.

Return: {"items": [ ... ]} where each item has:
  "type":        one of "action_item", "decision", "idea", "cancelled".
                 action_item = a concrete task someone must do.
                 decision    = a choice the team agreed on, not a task.
                 idea        = a vague maybe/someday suggestion, not committed.
                 cancelled   = something proposed then explicitly dropped.
  "task":        short imperative description of the item.
  "owner_raw":   the person responsible exactly as named in the transcript,
                 or null if no clear owner (e.g. "someone").
  "due_date_raw":the deadline exactly as spoken (e.g. "Thursday", "today",
                 "tomorrow"), or null.
  "priority":    "High", "Medium", or "Low". Default "Medium". Use "High"
                 only if urgency is clearly stated.
  "source_quote":the exact sentence(s) from the transcript this came from.
  "confidence":  0.0 to 1.0, how sure you are this is a real, actionable item.

Rules:
- Only "action_item" items should ever become tickets later; still return
  decisions, ideas, and cancelled items so they can be shown but not actioned.
- If the owner is ambiguous (e.g. two people share a name) or missing, keep
  owner_raw as spoken (or null) and LOWER the confidence.
- Never invent tasks, owners, or dates not supported by the transcript.
- Be conservative: if it is not a real commitment, classify it as idea or
  decision, not action_item."""

ALLOWED_TYPES = {"action_item", "decision", "idea", "cancelled"}
ALLOWED_PRIORITIES = {"High", "Medium", "Low"}
REQUIRED_FIELDS = {
    "type",
    "task",
    "owner_raw",
    "due_date_raw",
    "priority",
    "source_quote",
    "confidence",
}


def extract_items(transcript_text: str) -> list[dict]:
    """Extract structured meeting items from transcript text."""
    client = get_llm_client()
    payload = client.generate_json(EXTRACTION_INSTRUCTIONS, transcript_text)
    return _validate_items_payload(payload)


def _validate_items_payload(payload: dict[str, Any]) -> list[dict]:
    """Validate LLM JSON shape and return items."""
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object.")

    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError('LLM response must contain an "items" list.')

    validated: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"Item {index} must be a JSON object.")

        missing = REQUIRED_FIELDS - item.keys()
        if missing:
            raise ValueError(f"Item {index} is missing required field(s): {', '.join(sorted(missing))}.")

        item_type = item["type"]
        if item_type not in ALLOWED_TYPES:
            raise ValueError(f"Item {index} has invalid type {item_type!r}.")

        priority = item["priority"]
        if priority not in ALLOWED_PRIORITIES:
            raise ValueError(f"Item {index} has invalid priority {priority!r}.")

        if not isinstance(item["task"], str) or not item["task"].strip():
            raise ValueError(f"Item {index} must have a non-empty string task.")

        if item["owner_raw"] is not None and not isinstance(item["owner_raw"], str):
            raise ValueError(f"Item {index} owner_raw must be a string or null.")

        if item["due_date_raw"] is not None and not isinstance(item["due_date_raw"], str):
            raise ValueError(f"Item {index} due_date_raw must be a string or null.")

        if not isinstance(item["source_quote"], str) or not item["source_quote"].strip():
            raise ValueError(f"Item {index} must have a non-empty string source_quote.")

        confidence = item["confidence"]
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            raise ValueError(f"Item {index} confidence must be a number from 0.0 to 1.0.")

        clean_item = dict(item)
        clean_item["task"] = item["task"].strip()
        clean_item["source_quote"] = item["source_quote"].strip()
        clean_item["confidence"] = float(confidence)
        validated.append(clean_item)

    return validated


def _print_summary(items: list[dict]) -> None:
    """Print readable grouped summary."""
    print(f"Extracted {len(items)} item(s).")

    for item_type in ("action_item", "decision", "idea", "cancelled"):
        group = [item for item in items if item["type"] == item_type]
        print(f"\n{item_type} ({len(group)}):")
        if not group:
            print("  - none")
            continue

        for item in group:
            owner = item.get("owner_raw") or "unassigned"
            due = item.get("due_date_raw") or "no due date"
            print(
                f"  - {item['task']} "
                f"| owner: {owner} | due: {due} | priority: {item['priority']} "
                f"| confidence: {item['confidence']:.2f}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract structured items from a meeting transcript.")
    parser.add_argument("--transcript", default="data/transcript.txt", help="Transcript file to read.")
    parser.add_argument("--out", default="data/action_items.json", help="JSON output path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    transcript_path = Path(args.transcript)
    out_path = Path(args.out)

    if not transcript_path.exists() or not transcript_path.is_file():
        print(f"Error: transcript file not found: {transcript_path}")
        return 1

    transcript_text = transcript_path.read_text(encoding="utf-8")
    items = extract_items(transcript_text)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"items": items}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    _print_summary(items)
    print(f"\nWrote: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
