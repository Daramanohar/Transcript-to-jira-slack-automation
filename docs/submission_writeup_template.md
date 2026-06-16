# Origin Medical Role Challenge Submission

## Candidate

Name:

Role: Workflow Automation Engineer Intern

## Input Used

Meeting notes / transcript:

Link:

Participants:

Meeting date:

## Problem Understanding

Origin Medical's clinical and product teams run several meetings every week. After each meeting, a coordinator manually identifies action items, creates Jira tickets, and posts a Slack summary. This takes 30-45 minutes per meeting and can lead to missed, misassigned, or forgotten action items.

My goal was to automate this end-to-end while keeping the workflow auditable and easy to recover when the transcript is incomplete.

## Solution Overview

The automation reads meeting notes or a transcript, extracts decisions, risks, and action items, creates one Jira ticket per action item, then posts a structured Slack summary after ticket creation.

I implemented the workflow as a Python CLI because it is simple to run locally, easy to deploy in GitHub Actions, and easy to explain during the follow-up call.

## Workflow

1. Input transcript or notes file.
2. Extract structured meeting data.
3. Create Jira tickets.
4. Post Slack summary.
5. Save proof artifacts for review.

## Tools Chosen

- Python for a portable automation script.
- Jira Cloud REST API for ticket creation.
- Slack incoming webhook for summary posting.
- Optional OpenAI structured extraction for higher-quality action item detection.
- Deterministic fallback extraction for demos and failure recovery.
- GitHub Actions for hosted execution.

## Assumptions

- The transcript or notes include enough context to identify at least some action items.
- Jira project key and issue type are provided through environment variables.
- Slack webhook posts to the correct review channel.
- If owner or due date is missing, the ticket should still be created and the ambiguity should be documented.

## Failure Handling

- If OpenAI extraction fails, the deterministic fallback extractor is used.
- If Jira credentials are missing in `auto` mode, the system creates dry-run ticket records instead of failing silently.
- If Slack credentials are missing in `auto` mode, the Slack payload is saved for proof.
- If live Jira ticket creation fails for an item, that failure is included in the Slack/report output.
- Every run writes artifacts to `artifacts/latest/` for debugging and auditability.

## Proof

Jira tickets:

Slack summary:

Run report:

Code repository:

## What I Would Improve With More Time

- Add a small web UI for uploading transcripts and previewing action items before ticket creation.
- Add account ID lookup for Jira assignee mapping.
- Add duplicate detection against existing Jira tickets.
- Add transcript ingestion from Google Docs or meeting recordings.
- Add a human approval step before posting to Jira in production.
