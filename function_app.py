"""Azure Functions HTTP wrapper for the meeting automation pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import azure.functions as func
from dotenv import load_dotenv

from src.pipeline import PipelineStageError, append_audit_log, ingest_transcript, run_pipeline_from_text

# Local development convenience. Azure App Settings already exist in environment;
# python-dotenv does not override existing env vars by default.
load_dotenv(override=False)

app = func.FunctionApp()


@app.route(route="run", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def run(req: func.HttpRequest) -> func.HttpResponse:
    """Run pipeline from posted transcript text or fallback data/transcript.txt."""
    try:
        body: dict[str, Any] = {}
        raw_body = req.get_body()
        if raw_body:
            body = req.get_json()
            if not isinstance(body, dict):
                body = {}

        dry_run = bool(body.get("dry_run", False))
        transcript_text = body.get("transcript")

        if isinstance(transcript_text, str) and transcript_text.strip():
            print("[1/5] Ingesting... transcript supplied in request body")
            transcript_label = "request body"
        else:
            print("[1/5] Ingesting... falling back to data/transcript.txt")
            transcript_path = Path("data/transcript.txt")
            transcript_text = ingest_transcript(transcript_path)
            transcript_label = str(transcript_path)
            print(f"[1/5] Ingesting... read {len(transcript_text):,} characters")

        summary = run_pipeline_from_text(
            transcript_text=transcript_text,
            dry_run=dry_run,
            skip_slack=False,
            skip_jira=False,
            transcript_label=transcript_label,
        )

        return _json_response(summary, status_code=200)

    except PipelineStageError as exc:
        _log_failure(exc.stage, str(exc))
        return _json_response({"ok": False, "stage": exc.stage, "error": str(exc)}, status_code=500)
    except Exception as exc:  # noqa: BLE001 - HTTP function should return readable JSON.
        _log_failure("pipeline", str(exc))
        return _json_response({"ok": False, "stage": "pipeline", "error": str(exc)}, status_code=500)


def _log_failure(stage: str, error: str) -> None:
    try:
        append_audit_log({"event": "azure_function_failed", "stage": stage, "error": error})
    except Exception:
        pass


def _json_response(payload: dict[str, Any], status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
    )
