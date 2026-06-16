from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ActionItem:
    title: str
    description: str
    owner: str | None = None
    due_date: str | None = None
    priority: str = "Medium"
    labels: list[str] = field(default_factory=list)
    source_quote: str | None = None
    confidence: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MeetingAnalysis:
    summary: str
    decisions: list[str]
    risks: list[str]
    action_items: list[ActionItem]
    extraction_method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "decisions": self.decisions,
            "risks": self.risks,
            "action_items": [item.to_dict() for item in self.action_items],
            "extraction_method": self.extraction_method,
        }


@dataclass
class JiraTicketResult:
    action_title: str
    created: bool
    key: str | None
    url: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SlackPostResult:
    posted: bool
    status_code: int | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AutomationResult:
    analysis: MeetingAnalysis
    jira_results: list[JiraTicketResult]
    slack_result: SlackPostResult
    output_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis.to_dict(),
            "jira_results": [result.to_dict() for result in self.jira_results],
            "slack_result": self.slack_result.to_dict(),
            "output_dir": self.output_dir,
        }
