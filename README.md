# Origin Medical Role Challenge Automation

This project is a working meeting-to-action automation for the Workflow Automation Engineer Intern role challenge.

It reads meeting notes or a transcript, extracts action items, creates Jira tickets for each item, then posts a structured Slack summary after ticket creation. It can run in two modes:

- `dry-run`: no external credentials required; writes Jira and Slack proof payloads to `artifacts/`
- `live`: uses Jira Cloud and Slack credentials from `.env`

## Problem In One Line

The organization loses 30-45 minutes after every meeting because someone manually reads notes, finds action items, creates Jira tickets, and posts a Slack summary. This automation removes that manual handoff.

## Architecture

```text
Meeting notes / transcript
        |
        v
Action-item extractor
  - OpenAI structured extraction when OPENAI_API_KEY exists
  - deterministic fallback extractor when it does not
        |
        v
Jira ticket creator
        |
        v
Slack summary poster
        |
        v
Artifacts for proof and submission
```

## Setup

Install ffmpeg first. faster-whisper needs ffmpeg installed on the system to decode audio/video.

Get a Gemini API key from Google AI Studio and set `GEMINI_API_KEY` in `.env` before running LLM extraction.

- Windows: `winget install ffmpeg` (or `choco install ffmpeg`)
- Mac: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

```powershell
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Quick Start

```powershell
python -m pip install -e .
python -m meeting_automation run --input data/sample_meeting_notes.md --mode dry-run
```

## How to run

- Install ffmpeg first (the one system dependency that commonly trips people up).
- Place the recording in the project folder, then run:
      python transcribe.py --input yourfile.mp4
- The first run downloads the ~460 MB "small" model once, then transcribes.
- Expected output: data/transcript.txt and data/transcript.pdf, and the script prints the absolute paths to both.
- This script does not label speakers. Since there are two known speakers, the user will manually prefix lines with "Asri:" / "Manohar:" in a quick pass after transcription — this helps downstream task-extraction attribute owners.
- If "small" accuracy is rough, re-run with --model medium (slower on CPU, cleaner output).

The command writes evidence to `artifacts/latest/`:

- `analysis.json`
- `jira_payloads.json`
- `jira_results.json`
- `slack_payload.json`
- `run_report.md`

## Run the full pipeline

```powershell
python -m src.pipeline --transcript data/transcript.txt
python -m src.pipeline --dry-run
```

`--dry-run` is a safe preview: it builds Jira payloads and Slack blocks without posting.

## Live Setup

1. Copy `.env.example` to `.env`.
2. Add Jira Cloud credentials.
3. Add a Slack incoming webhook URL.
4. Optional: add `OPENAI_API_KEY` for stronger extraction.
5. Run:

```powershell
python -m meeting_automation run --input data/your_real_meeting_notes.md --mode live
```

## Submission Package

Use `docs/submission_writeup_template.md` as the base for the final PDF. The final PDF should link to:

- your real transcript or notes document
- this source code repository
- screenshots of Jira tickets
- screenshot of the Slack summary message
- generated artifact files or a screen recording

## Deploy to Azure

This repo includes an Azure Functions Python v2 HTTP trigger at `POST /api/run` with function-key auth.

Configure these Azure App Settings (do not commit secrets):

- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `LLM_PROVIDER`
- `JIRA_BASE_URL`
- `JIRA_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_PROJECT_KEY`
- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL`

For local Azure Functions testing, copy `local.settings.json.example` to `local.settings.json` and fill real values. `.env` is still loaded locally, but real environment variables/Azure App Settings take precedence.

## Test the live deployment (Azure)

The pipeline is deployed as an HTTP-triggered Azure Function. You can run the
entire flow in the cloud with a single request, with no local setup required.

**Endpoint:** `https://wfae-meeting-automation.azurewebsites.net/api/run`

The request body accepts:
- `dry_run` (bool): preview only, creates no tickets and posts nothing.
- `use_saved_items` (bool): run from the frozen extraction for a reproducible result.

**Safe preview (PowerShell):**
```powershell
$body = '{"dry_run": true}'
Invoke-RestMethod -Uri "https://wfae-meeting-automation.azurewebsites.net/api/run?code=YOUR_FUNCTION_KEY" -Method Post -Body $body -ContentType "application/json"
```

**curl:**
```bash
curl -X POST "https://wfae-meeting-automation.azurewebsites.net/api/run?code=YOUR_FUNCTION_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

A successful response returns a JSON summary: items extracted, tickets
created/skipped/failed, and whether the Slack summary was posted.

Reviewers should replace `YOUR_FUNCTION_KEY` with the key provided in the submission PDF.

## Deployment Options

Recommended for this challenge: GitHub Actions.

The workflow at `.github/workflows/run-meeting-automation.yml` can run the automation manually from a transcript file committed to the repo, using GitHub Secrets for Jira, Slack, and OpenAI credentials.

For a simple hosted job, use the included `Dockerfile` on Render, Railway, or any container host.
