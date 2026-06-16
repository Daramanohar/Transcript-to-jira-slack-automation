# Setup And Demo Guide

## Step 1: Understand The Challenge

The manual process is:

1. Read a transcript after a meeting.
2. Identify action items.
3. Create one Jira ticket per action item.
4. Post a Slack summary after tickets are created.

The automation mirrors that exact flow so the evaluator can trace the solution from input to output.

## Step 2: Capture Real Meeting Notes

Run a 10-15 minute call with one other person. Capture:

- discussion points
- decisions
- action items
- owners
- due dates when available
- risks or blockers

Store the notes in a shared Google Doc with view access, then export or copy them into a local Markdown file such as `data/real_meeting_notes.md`.

## Step 3: Run Dry-Run First

```powershell
python -m pip install -e .
python -m meeting_automation run --input data/real_meeting_notes.md --meeting-title "Real Role Challenge Meeting" --mode dry-run
```

Use this to verify extraction quality before touching Jira or Slack.

## Step 4: Configure Jira

Create a free Jira Cloud site and project. Then create an API token from your Atlassian account.

Add these values to `.env`:

```text
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-api-token
JIRA_PROJECT_KEY=ABC
JIRA_ISSUE_TYPE=Task
```

## Step 5: Configure Slack

Create a Slack app with an incoming webhook and install it to your test channel.

Add this to `.env`:

```text
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

## Step 6: Optional AI Extraction

Add `OPENAI_API_KEY` to `.env` to use structured LLM extraction. Without the key, the deterministic fallback still runs.

## Step 7: Run Live

```powershell
python -m meeting_automation run --input data/real_meeting_notes.md --meeting-title "Real Role Challenge Meeting" --mode live
```

## Step 8: Capture Proof

Capture:

- screenshot of created Jira tickets
- screenshot of Slack summary message
- `artifacts/latest/run_report.md`
- a short screen recording if possible

## Step 9: Submit

Create one PDF using `docs/submission_writeup_template.md` and include hyperlinks to:

- meeting notes Google Doc
- source code repository
- Jira screenshots or recording
- Slack screenshot
- generated artifacts
