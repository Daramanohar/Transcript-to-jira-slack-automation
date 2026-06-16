from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import Settings
from .models import SlackPostResult


class SlackClient:
    def __init__(self, settings: Settings, dry_run: bool) -> None:
        self.settings = settings
        self.dry_run = dry_run or not settings.slack_ready

    def post(self, payload: dict) -> SlackPostResult:
        if self.dry_run:
            return SlackPostResult(posted=True, status_code=None)

        assert self.settings.slack_webhook_url is not None
        request = urllib.request.Request(
            self.settings.slack_webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status >= 300:
                    return SlackPostResult(posted=False, status_code=response.status, error=body)
                return SlackPostResult(posted=True, status_code=response.status)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return SlackPostResult(posted=False, status_code=exc.code, error=body)
        except Exception as exc:
            return SlackPostResult(posted=False, status_code=None, error=str(exc))
