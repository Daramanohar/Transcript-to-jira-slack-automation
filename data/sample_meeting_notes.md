# Product Review Meeting Notes

Date: 2026-06-13
Participants: Asha, Ravi, Meera

Context:
The product and clinical team reviewed the upcoming fetal ultrasound annotation workflow. The group discussed onboarding two new clinicians, reducing annotation turnaround time, and improving how uncertain cases are escalated.

Decisions:
- Use the existing Jira project for workflow follow-up instead of creating a separate tracker.
- Keep the first release focused on annotation upload, review assignment, and uncertainty escalation.
- Pilot with 20 retrospective ultrasound studies before expanding to live cases.

Discussion:
Asha said the annotation upload screen is almost ready but still needs validation copy for incomplete scans. Ravi asked whether the reviewer assignment logic can avoid assigning the same clinician twice in a row. Meera mentioned that the clinical reviewers need a one-page guide before the pilot begins.

Action items:
- Asha will draft validation copy for incomplete scan uploads by 2026-06-17.
- Ravi will add reviewer rotation rules to the technical plan by 2026-06-18.
- Meera will create a one-page clinician pilot guide by 2026-06-19.
- Ravi will confirm whether Jira labels should include `clinical-pilot` and `annotation-workflow`.
- Asha will schedule the pilot readiness review for next week.

Risks:
- If reviewer assignment is unclear, pilot feedback may be noisy.
- If the clinician guide is late, onboarding will slip.
