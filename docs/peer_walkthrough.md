# Peer Walkthrough Script

Use this script during the follow-up call.

## 1. Start With The Problem

"The current post-meeting process is manual: read notes, find action items, create Jira tickets, and post Slack updates. The risk is not just time loss but missed ownership, wrong assignment, and weak follow-through."

## 2. Explain The Design

"I mirrored the real workflow instead of building an unrelated demo. The automation takes a transcript as input, extracts structured action items, creates Jira tickets, and only then posts Slack."

## 3. Show The Input

Open the real meeting notes Google Doc and point out:

- decisions
- ambiguous action items
- owners
- due dates

## 4. Show The Run

Run:

```powershell
python -m meeting_automation run --input data/real_meeting_notes.md --meeting-title "Real Role Challenge Meeting" --mode live
```

Then open `artifacts/latest/run_report.md`.

## 5. Show Jira

Open the project board and show each ticket. Explain that owner and due date are preserved in the description because Jira account assignment requires account ID mapping.

## 6. Show Slack

Open the Slack channel and show the summary message with ticket links, decisions, and risks.

## 7. Defend Failure Handling

"The system does not depend on a perfect transcript. Missing owner or due date is documented instead of guessed. Missing credentials trigger dry-run behavior in auto mode. Live mode fails loudly if required credentials are missing."

## 8. Explain Scaling

"For production, I would add approval before creating tickets, duplicate detection, Jira account mapping, and direct ingestion from Google Docs or meeting transcription tools."
