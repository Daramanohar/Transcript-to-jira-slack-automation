from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meeting-automation",
        description="Extract meeting action items, create Jira tickets, and post a Slack summary.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full meeting automation.")
    run.add_argument("--input", required=True, help="Path to transcript or meeting notes.")
    run.add_argument("--out", default="artifacts/latest", help="Output directory for proof artifacts.")
    run.add_argument("--meeting-title", default="Meeting", help="Human-readable meeting title.")
    run.add_argument("--meeting-date", default=None, help="Optional meeting date in YYYY-MM-DD format.")
    run.add_argument(
        "--mode",
        choices=["auto", "dry-run", "live"],
        default="auto",
        help="auto uses real integrations when credentials exist; dry-run never calls external services.",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        settings = Settings.from_env()
        result = run_pipeline(
            input_path=Path(args.input),
            output_dir=Path(args.out),
            settings=settings,
            meeting_title=args.meeting_title,
            meeting_date=args.meeting_date,
            mode=args.mode,
        )

        print("Automation complete.")
        print(f"Output directory: {result.output_dir}")
        print(f"Action items found: {len(result.analysis.action_items)}")
        print(f"Jira results: {len(result.jira_results)}")
        print(f"Slack posted: {result.slack_result.posted}")
