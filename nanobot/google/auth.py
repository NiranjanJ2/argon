"""Per-account Google OAuth2 token management.

Each account gets its own token file:
  <workspace>/google/<account>/token.json

The client_secrets.json (downloaded from Google Cloud Console) lives at:
  <workspace>/google/client_secrets.json

Usage:
  # First-time auth for an account (opens browser):
  auth = GoogleAuth(workspace)
  creds = auth.authenticate("work")

  # In tools (non-interactive, raises if not yet authed):
  creds = auth.get_credentials("work")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

# Scopes per account — only the services each account actually uses.
ACCOUNT_SCOPES: dict[str, list[str]] = {
    "personal": [
        "https://www.googleapis.com/auth/drive.readonly",
    ],
    "work": [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/tasks",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
    "school": [
        "https://www.googleapis.com/auth/classroom.courses.readonly",
        "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
        "https://www.googleapis.com/auth/classroom.announcements.readonly",
        "https://www.googleapis.com/auth/classroom.rosters.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
}


class GoogleAuth:
    """Manages OAuth2 credentials for multiple Google accounts."""

    def __init__(self, workspace: Path) -> None:
        self._base = workspace / "google"
        self._secrets_path = self._base / "client_secrets.json"

    def _token_path(self, account: str) -> Path:
        return self._base / account / "token.json"

    def get_credentials(self, account: str) -> "Credentials":
        """Return valid credentials for *account*. Raises if not yet authenticated."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if account not in ACCOUNT_SCOPES:
            raise ValueError(f"Unknown account '{account}'. Known: {list(ACCOUNT_SCOPES)}")

        token_path = self._token_path(account)
        if not token_path.exists():
            raise RuntimeError(
                f"Account '{account}' is not authenticated. "
                f"Run: nanobot google-auth {account}"
            )

        creds = Credentials.from_authorized_user_file(str(token_path), ACCOUNT_SCOPES[account])

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save(account, creds)
            else:
                raise RuntimeError(
                    f"Credentials for '{account}' are invalid and cannot be refreshed. "
                    f"Run: nanobot google-auth {account}"
                )

        return creds

    def authenticate(self, account: str) -> "Credentials":
        """Run the OAuth2 consent flow for *account* (interactive, opens browser)."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if account not in ACCOUNT_SCOPES:
            raise ValueError(f"Unknown account '{account}'. Known: {list(ACCOUNT_SCOPES)}")

        if not self._secrets_path.exists():
            raise FileNotFoundError(
                f"client_secrets.json not found at {self._secrets_path}. "
                "Download it from Google Cloud Console → APIs & Services → Credentials."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._secrets_path),
            scopes=ACCOUNT_SCOPES[account],
            redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        )
        auth_url, _ = flow.authorization_url(prompt="consent")
        print(f"\nOpen this URL in your browser:\n\n  {auth_url}\n")
        code = input("Paste the authorization code here: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials
        self._save(account, creds)
        return creds

    def _save(self, account: str, creds: "Credentials") -> None:
        token_path = self._token_path(account)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    def is_authenticated(self, account: str) -> bool:
        return self._token_path(account).exists()
