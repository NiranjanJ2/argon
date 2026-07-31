"""Per-account Google OAuth2 token management.

Layout under the workspace::

    google/client_secrets.json    OAuth client, "Desktop app" type
    google/<account>/token.json   credentials for one account (mode 0600)
    google/<account>/auth_error   last hard auth failure, cleared on success

Interactive auth uses the loopback flow on a fixed port. The out-of-band
("urn:ietf:wg:oauth:2.0:oob") flow Google shut down in 2022 is gone.

Usage::

    auth = GoogleAuth(workspace)
    auth.authenticate("work")       # interactive, prints a URL
    creds = auth.get_credentials("work")   # raises GoogleAuthExpired if dead
    auth.status()                   # {'work': 'ok', 'school': 'expired', ...}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

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
    "trigger": [
        "https://www.googleapis.com/auth/gmail.send",
    ],
}

#: Accounts that may be unauthenticated without anything being wrong.
#: ``trigger`` only ever existed to mail an SMS gateway that tripped a Shortcut;
#: the iOS app drives Screen Time directly now, so a dead grant here is expected
#: rather than a fault. Re-auth still works if the mail path is wanted again.
OPTIONAL_ACCOUNTS = frozenset({"trigger"})

#: Loopback port used by the consent flow. Fixed so it can be SSH-forwarded.
LOOPBACK_PORT = 8765

_TESTING_MODE_HINT = (
    "refresh token rejected (invalid_grant). If every account fails this way, the "
    "Google Cloud OAuth consent screen is still in Testing publishing status, which "
    "hard-expires refresh tokens after 7 days — publish the app to In production"
)


class GoogleAuthExpired(RuntimeError):
    """An account's grant is missing or dead; interactive re-auth is required."""

    def __init__(self, account: str, reason: str = "") -> None:
        self.account = account
        self.reason = reason
        self.remedy = f"run `argon google-auth {account}`"
        message = f"Google {account} account needs re-authentication — {self.remedy}"
        if reason:
            message += f" ({reason})"
        super().__init__(message)


class GoogleAuthUnavailable(RuntimeError):
    """Token refresh failed for a transient reason (network, Google outage)."""

    def __init__(self, account: str, reason: str = "") -> None:
        self.account = account
        self.reason = reason
        super().__init__(
            f"Google {account} account is temporarily unreachable — "
            f"the grant is probably fine, retry shortly ({reason})"
        )


class GoogleAuth:
    """Manages OAuth2 credentials for multiple Google accounts."""

    def __init__(self, workspace: Path) -> None:
        self._base = workspace / "google"
        self._secrets_path = self._base / "client_secrets.json"

    # -- paths ---------------------------------------------------------

    def _token_path(self, account: str) -> Path:
        return self._base / account / "token.json"

    def _error_path(self, account: str) -> Path:
        return self._base / account / "auth_error"

    @staticmethod
    def _check_account(account: str) -> None:
        if account not in ACCOUNT_SCOPES:
            raise ValueError(f"Unknown account '{account}'. Known: {list(ACCOUNT_SCOPES)}")

    # -- reading credentials -------------------------------------------

    def get_credentials(self, account: str) -> "Credentials":
        """Return valid credentials for *account*, refreshing if needed.

        Raises ``GoogleAuthExpired`` when the grant is gone (re-auth required)
        and ``GoogleAuthUnavailable`` when the refresh merely could not reach
        Google. The two are deliberately distinct: only the first is fatal.
        """
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        self._check_account(account)

        token_path = self._token_path(account)
        if not token_path.exists():
            raise GoogleAuthExpired(account, "no token stored")

        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path), ACCOUNT_SCOPES[account]
            )
        except (ValueError, OSError) as exc:  # ValueError covers malformed JSON
            raise GoogleAuthExpired(account, f"token file unreadable: {exc}") from exc

        if creds.valid:
            return creds
        if not creds.refresh_token:
            raise GoogleAuthExpired(account, "no refresh token stored")

        try:
            creds.refresh(Request())
        except RefreshError as exc:
            reason = _TESTING_MODE_HINT if "invalid_grant" in str(exc) else str(exc)
            self._record_error(account, reason)
            raise GoogleAuthExpired(account, reason) from exc
        except TransportError as exc:
            # Network blip — do NOT record an error, the grant may still be good.
            raise GoogleAuthUnavailable(account, str(exc)) from exc

        self._save(account, creds)
        logger.debug(f"Refreshed Google credentials for '{account}'")
        return creds

    # -- interactive consent -------------------------------------------

    def authenticate(self, account: str, port: int = LOOPBACK_PORT) -> "Credentials":
        """Run the OAuth consent flow for *account* over a loopback redirect.

        Headless-safe: no browser is launched. The URL is printed so the human
        can open it on their own machine after forwarding *port* over SSH.
        """
        from google_auth_oauthlib.flow import InstalledAppFlow

        self._check_account(account)

        if not self._secrets_path.exists():
            raise FileNotFoundError(
                f"client_secrets.json not found at {self._secrets_path}. "
                "Download it from Google Cloud Console → APIs & Services → "
                "Credentials → OAuth 2.0 Client IDs (Desktop app) → Download JSON."
            )

        # Google routinely grants a superset of the requested scopes (openid,
        # userinfo/*); without this oauthlib aborts the exchange over the diff.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._secrets_path), scopes=ACCOUNT_SCOPES[account]
        )

        prompt = (
            f"\nAuthorize the Google '{account}' account.\n\n"
            f"  1. On your own machine (not this server), open an SSH tunnel:\n"
            f"       ssh -L {port}:localhost:{port} <this-server>\n"
            f"  2. With that tunnel open, visit this URL in your browser:\n\n"
            "       {url}\n\n"
            f"  3. Approve every requested permission. The final redirect must\n"
            f"     reach http://localhost:{port} through the tunnel, or this\n"
            f"     command will keep waiting.\n"
        )

        try:
            creds = flow.run_local_server(
                host="localhost",
                port=port,
                open_browser=False,
                authorization_prompt_message=prompt,
                success_message=(
                    f"Argon: '{account}' authorized. You can close this tab."
                ),
                access_type="offline",
                prompt="consent",
            )
        except OSError as exc:
            raise RuntimeError(
                f"Could not bind the OAuth loopback server to localhost:{port} ({exc}). "
                f"Free the port, or pass a different one."
            ) from exc

        if not creds.refresh_token:
            logger.warning(
                f"Google '{account}' returned no refresh token; Argon will lose "
                "access as soon as the access token expires."
            )
        self._save(account, creds)
        logger.info(f"Google '{account}' authenticated → {self._token_path(account)}")
        return creds

    # -- status --------------------------------------------------------

    def status(self) -> dict[str, str]:
        """Auth state of every known account. Cheap: no network, no refresh."""
        return {account: self.account_status(account) for account in ACCOUNT_SCOPES}

    def account_status(self, account: str) -> str:
        """``'ok'`` | ``'expired'`` | ``'missing'`` for *account*. No network."""
        self._check_account(account)

        token_path = self._token_path(account)
        if not token_path.exists():
            return "missing"
        if self._error_path(account).exists():
            return "expired"

        try:
            data = json.loads(token_path.read_text())
        except (OSError, json.JSONDecodeError):
            return "missing"

        if not data.get("refresh_token"):
            return "expired"
        granted = set(data.get("scopes") or [])
        if granted and set(ACCOUNT_SCOPES[account]) - granted:
            return "expired"  # scopes were widened since this token was minted
        return "ok"

    def status_message(self, account: str) -> str | None:
        """One-line reason *account* is unusable, or ``None`` when it looks fine."""
        state = self.account_status(account)
        if state == "ok":
            return None
        if state == "missing":
            return (
                f"Google {account} account is not connected — "
                f"run `argon google-auth {account}`"
            )
        return str(GoogleAuthExpired(account, self.last_error(account) or ""))

    def verify(self, account: str) -> tuple[str, str | None]:
        """Actually exercise the grant. Returns ``(state, detail)``.

        ``account_status`` only inspects the token file, so a revoked grant
        still reads as ``ok`` until something tries to use it — which is how
        four dead accounts went unnoticed for three months. ``doctor`` calls
        this instead; it costs one network round-trip per account.
        """
        state = self.account_status(account)
        if state != "ok":
            return state, self.status_message(account)
        try:
            self.get_credentials(account)
        except GoogleAuthExpired as e:
            return "expired", str(e)
        except GoogleAuthUnavailable as e:
            return "unreachable", str(e)
        except Exception as e:  # noqa: BLE001 — doctor must never crash
            return "error", str(e)
        return "ok", None

    def last_error(self, account: str) -> str | None:
        """The recorded reason an account went bad, if any."""
        path = self._error_path(account)
        try:
            return path.read_text().strip() or None
        except OSError:
            return None

    def is_authenticated(self, account: str) -> bool:
        return self.account_status(account) == "ok"

    # -- persistence ---------------------------------------------------

    def _save(self, account: str, creds: "Credentials") -> None:
        """Write credentials atomically with owner-only permissions."""
        token_path = self._token_path(account)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = token_path.with_suffix(".json.tmp")
        tmp.write_text(creds.to_json())
        tmp.chmod(0o600)
        tmp.replace(token_path)
        self._error_path(account).unlink(missing_ok=True)

    def _record_error(self, account: str, reason: str) -> None:
        """Remember a hard failure so ``status()`` can report it without a refresh."""
        path = self._error_path(account)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reason)
        logger.error(f"Google '{account}' grant is dead: {reason}")
