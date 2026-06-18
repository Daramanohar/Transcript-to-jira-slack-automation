#!/usr/bin/env python3
"""List Jira user account IDs for config/owners.json."""

from __future__ import annotations

import base64
import sys
from os import getenv

import requests
from dotenv import load_dotenv


def _required_env(name: str) -> str:
    """Read required env var or exit with clear error."""
    value = getenv(name, "").strip()
    if not value:
        print(f"Error: {name} is not set. Add it to .env.", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> int:
    load_dotenv()

    base_url = _required_env("JIRA_BASE_URL").rstrip("/")
    email = _required_env("JIRA_EMAIL")
    api_token = _required_env("JIRA_API_TOKEN")

    auth_raw = f"{email}:{api_token}".encode("utf-8")
    auth_encoded = base64.b64encode(auth_raw).decode("ascii")

    headers = {
        "Authorization": f"Basic {auth_encoded}",
        "Accept": "application/json",
    }
    url = f"{base_url}/rest/api/3/users/search?maxResults=200"

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"Error: Jira API returned status {response.status_code}", file=sys.stderr)
            print(response.text, file=sys.stderr)
            return 1

        users = response.json()
        if not isinstance(users, list):
            print("Error: Jira API response was not a user list.", file=sys.stderr)
            return 1

        for user in users:
            if user.get("accountType") != "atlassian":
                continue

            display_name = user.get("displayName") or ""
            email_address = user.get("emailAddress") or "hidden"
            account_id = user.get("accountId") or ""
            print(f"{display_name} | {email_address} | {account_id}")

        print('\nCopy the accountId values into config/owners.json')
        return 0

    except requests.RequestException as exc:
        print(f"Error: failed to call Jira API: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: failed to parse Jira API response as JSON: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI should report readable failures.
        print(f"Error: unexpected failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
