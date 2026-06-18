#!/usr/bin/env python3
"""End-to-end orchestrator: ingest -> extract -> resolve -> Jira -> Slack."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if __package__:
    from .extract import extract_items
    from .jira_client import create_tickets
    from .resolve import resolve_items
    from .slack_client import post_summary
else:
    from extract import extract_items
    from jira_client import create_tickets
    from resolve import resolve_items
    from slack_client import post_summary

DATA_DIR = Path("data")
ACTION_ITEMS_PATH = DATA_DIR / "action_items.json"
RESOLVED_ITEMS_PATH = DATA_DIR / "resolved_items.json"
CREATE_RESULT_PATH = DATA_DIR / "create_result.json"
SLACK_RESULT_PATH = DATA_DIR / "slack_result.json"
AUDIT_LOG_PATH = DATA_DIR / "audit_log.json"


def ingest_transcript(path: Path) -> str:
    """Read transcript text from disk."""
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"transcript file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"transcript file is empty: {path}")
    return text


def write_json(path: Path, payload: Any) -> None:
    """Write pretty JSON artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_audit_log(record: dict[str, Any]) -> None:
    """Append pipeline event to audit log, using src/audit.py if present."""
    enriched = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }

    try:
        import audit  # type: ignore[import-not-found]

        if hasattr(audit, "append"):
            audit.append(enriched)  # type: ignore[attr-defined]
            return
        if hasattr(audit, "append_audit_log"):
            audit.append_audit_log(enriched)  # type: ignore[attr-defined]
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
    existing.append(enriched)
    AUDIT_LOG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class PipelineStageError(RuntimeError):
    """Pipeline failed at a named stage."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(message)


def fail_stage(stage: str, exc: BaseException) -> int:
    """Log stage failure and return CLI failure code."""
    message = str(exc)
    append_audit_log(
        {
            "event": "pipeline_stage_failed",
            "stage": stage,
            "error": message,
        }
    )
    print(f"Error: {stage} failed: {message}", file=sys.stderr)
    return 1


def _empty_create_result() -> dict[str, Any]:
    return {
        "created": 0,
        "skipped": 0,
        "failed": 0,
        "created_issue_keys": [],
        "errors": [],
        "items": [],
    }


def run_pipeline_from_text(
    transcript_text: str,
    dry_run: bool = False,
    skip_slack: bool = False,
    skip_jira: bool = False,
    transcript_label: str = "inline transcript",
) -> dict[str, Any]:
    """Run extract -> resolve -> Jira -> Slack from transcript text."""
    summary: dict[str, Any] = {
        "items_extracted": 0,
        "tickets_created": 0,
        "tickets_skipped": 0,
        "tickets_failed": 0,
        "slack_posted": False,
        "dry_run": dry_run,
        "skip_slack": skip_slack,
        "skip_jira": skip_jira,
    }

    if not transcript_text.strip():
        raise PipelineStageError("ingest", "transcript text is empty")

    print("[2/5] Extracting...")
    try:
        items = extract_items(transcript_text)
        write_json(ACTION_ITEMS_PATH, {"items": items})
    except Exception as exc:
        raise PipelineStageError("extract", str(exc)) from exc
    summary["items_extracted"] = len(items)
    print(f"[2/5] Extracting... found {len(items)} items")
    print(f"      wrote {ACTION_ITEMS_PATH}")

    print("[3/5] Resolving...")
    try:
        resolved = resolve_items(items)
        write_json(RESOLVED_ITEMS_PATH, {"items": resolved})
    except Exception as exc:
        raise PipelineStageError("resolve", str(exc)) from exc
    create_candidates = sum(1 for item in resolved if item.get("should_create_ticket"))
    review_count = sum(1 for item in resolved if item.get("needs_human_review"))
    print(f"[3/5] Resolving... {create_candidates} ticket candidate(s), {review_count} need review")
    print(f"      wrote {RESOLVED_ITEMS_PATH}")

    create_result: dict[str, Any] = _empty_create_result()

    if skip_jira:
        print("[4/5] Creating Jira tickets... skipped (--skip-jira)")
        print("[5/5] Posting Slack summary... skipped (--skip-jira)")
    else:
        print("[4/5] Creating Jira tickets...")
        try:
            create_result = create_tickets(resolved, dry_run=dry_run)
            write_json(CREATE_RESULT_PATH, create_result)
        except Exception as exc:
            raise PipelineStageError("jira", str(exc)) from exc

        summary["tickets_created"] = int(create_result.get("created", 0))
        summary["tickets_skipped"] = int(create_result.get("skipped", 0))
        summary["tickets_failed"] = int(create_result.get("failed", 0))
        print(
            "[4/5] Creating Jira tickets... "
            f"created {summary['tickets_created']}, skipped {summary['tickets_skipped']}, "
            f"failed {summary['tickets_failed']}"
        )
        print(f"      wrote {CREATE_RESULT_PATH}")

        if summary["tickets_failed"]:
            raise PipelineStageError("jira", f"{summary['tickets_failed']} item(s); not posting Slack summary")

        if skip_slack:
            print("[5/5] Posting Slack summary... skipped (--skip-slack)")
        else:
            print("[5/5] Posting Slack summary...")
            try:
                slack_result = post_summary(create_result, resolved, dry_run=dry_run)
                write_json(SLACK_RESULT_PATH, slack_result)
            except Exception as exc:
                raise PipelineStageError("slack", str(exc)) from exc

            summary["slack_posted"] = bool(slack_result.get("ok")) and not dry_run
            status = "dry-run preview built" if dry_run else "posted"
            print(f"[5/5] Posting Slack summary... {status}")
            print(f"      wrote {SLACK_RESULT_PATH}")

    append_audit_log(
        {
            "event": "pipeline_completed",
            "transcript": transcript_label,
            "summary": summary,
        }
    )
    return summary


def run_pipeline(transcript_path: Path, dry_run: bool = False, skip_slack: bool = False, skip_jira: bool = False) -> dict[str, Any]:
    """Run full workflow from transcript file and return summary."""
    print("[1/5] Ingesting...")
    try:
        transcript_text = ingest_transcript(transcript_path)
    except Exception as exc:
        raise PipelineStageError("ingest", str(exc)) from exc
    print(f"[1/5] Ingesting... read {len(transcript_text):,} characters")

    return run_pipeline_from_text(
        transcript_text=transcript_text,
        dry_run=dry_run,
        skip_slack=skip_slack,
        skip_jira=skip_jira,
        transcript_label=str(transcript_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ingest -> extract -> resolve -> Jira -> Slack pipeline.")
    parser.add_argument("--transcript", default="data/transcript.txt", help="Transcript text file.")
    parser.add_argument("--dry-run", action="store_true", help="Preview Jira payloads and Slack blocks without posting.")
    parser.add_argument("--skip-slack", action="store_true", help="Run everything except Slack post.")
    parser.add_argument("--skip-jira", action="store_true", help="Run extract + resolve only.")
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    transcript_path = Path(args.transcript)

    try:
        summary = run_pipeline(
            transcript_path=transcript_path,
            dry_run=args.dry_run,
            skip_slack=args.skip_slack,
            skip_jira=args.skip_jira,
        )
    except PipelineStageError as exc:
        return fail_stage(exc.stage, exc)
    except Exception as exc:
        return fail_stage("pipeline", exc)

    print("\nFinal summary:")
    print(f"items extracted: {summary['items_extracted']}")
    print(f"tickets created: {summary['tickets_created']}")
    print(f"tickets skipped: {summary['tickets_skipped']}")
    print(f"tickets failed: {summary['tickets_failed']}")
    print(f"slack posted: {'yes' if summary['slack_posted'] else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
