from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    jira_base_url: str | None
    jira_email: str | None
    jira_api_token: str | None
    jira_project_key: str | None
    jira_issue_type: str
    slack_webhook_url: str | None
    organization_name: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        return cls(
            openai_api_key=_empty_to_none(os.getenv("OPENAI_API_KEY")),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
            jira_base_url=_normalize_base_url(_empty_to_none(os.getenv("JIRA_BASE_URL"))),
            jira_email=_empty_to_none(os.getenv("JIRA_EMAIL")),
            jira_api_token=_empty_to_none(os.getenv("JIRA_API_TOKEN")),
            jira_project_key=_empty_to_none(os.getenv("JIRA_PROJECT_KEY")),
            jira_issue_type=os.getenv("JIRA_ISSUE_TYPE", "Task"),
            slack_webhook_url=_empty_to_none(os.getenv("SLACK_WEBHOOK_URL")),
            organization_name=os.getenv("ORGANIZATION_NAME", "Origin Medical"),
        )

    @property
    def jira_ready(self) -> bool:
        return all(
            [
                self.jira_base_url,
                self.jira_email,
                self.jira_api_token,
                self.jira_project_key,
            ]
        )

    @property
    def slack_ready(self) -> bool:
        return bool(self.slack_webhook_url)


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    return value.rstrip("/")
