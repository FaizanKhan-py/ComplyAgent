"""Email in and out: Microsoft Graph (Microsoft 365), or record-only.

    RecordOnly    EMAIL_PROVIDER=none (the default): Send records the email; nothing leaves the
                  system, and replies are pasted in by hand
    GraphMailer   EMAIL_PROVIDER=graph: an app registration (client credentials) sends from the
                  collections mailbox and reads its inbox for the customers' replies

    send(to, subject, body)   -> {delivered, external_id, external_conversation_id}
    unread()                  -> the inbox's unread emails, as plain text
    mark_read(id)

Sending creates the email as a draft first and then sends it, because only a draft has an id
and a conversationId; the conversationId is how a customer's reply is matched to the thread.

Outside production every email goes to EMAIL_REDIRECT_TO instead of the customer (config.py
refuses to start without it), so a test can never reach a real person.
"""

from __future__ import annotations

import logging
import re
import threading
import time

import httpx

from ..core.config import Settings

logger = logging.getLogger("complyagent.mail")

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
UNREAD_PAGE = 50
# Where the customer's reply ends and the quoted earlier email begins.
QUOTE_START = re.compile(
    r"^\s*(?:-{2,}\s*Original Message\s*-{2,}|From:\s.+|On .+ wrote:|>.*|_{8,})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class MailError(Exception):
    """The mailbox could not be used. `message` is safe to show to an administrator."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def reply_text(body: str) -> str:
    """The customer's own words: the text above the quoted earlier email."""
    match = QUOTE_START.search(body)
    text = body[: match.start()] if match else body
    return text.strip() or body.strip()


class RecordOnly:
    provider = "none"
    mailbox = None
    redirect_to = None

    def send(self, to: str, subject: str, body: str, customer_id: int) -> dict:
        return {"delivered": False, "external_id": None, "external_conversation_id": None}

    def unread(self) -> list[dict]:
        raise MailError("Email is not connected (EMAIL_PROVIDER=none): paste replies by hand.")

    def mark_read(self, message_id: str) -> None:
        return None


class GraphMailer:
    provider = "graph"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.tenant = settings.graph_tenant_id
        self.client_id = settings.graph_client_id
        self._secret = settings.graph_client_secret
        self.mailbox = settings.graph_mailbox
        self.redirect_to = settings.email_redirect_to or None
        self.http = client or httpx.Client(timeout=30)
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def _bearer(self) -> str:
        with self._lock:
            if self._token and self._token[1] > time.time() + 60:
                return self._token[0]
            response = self.http.post(
                TOKEN_URL.format(tenant=self.tenant),
                data={
                    "client_id": self.client_id,
                    "client_secret": self._secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            if response.status_code != 200:
                raise MailError(
                    f"Microsoft sign-in failed ({response.status_code}). Check GRAPH_TENANT_ID, "
                    "GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET."
                )
            body = response.json()
            self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
            return self._token[0]

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._bearer()}", **kw.pop("headers", {})}
        try:
            response = self.http.request(method, f"{GRAPH}{path}", headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise MailError(
                f"Microsoft Graph could not be reached ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 400:
            code = (response.json().get("error") or {}).get("code", "") if response.content else ""
            raise MailError(f"Microsoft Graph refused {method} {path.split('?')[0]} "
                            f"({response.status_code} {code}).")  # fmt: skip
        return response

    def send(self, to: str, subject: str, body: str, customer_id: int) -> dict:
        recipient = self.redirect_to or to
        if self.redirect_to:
            # The real address stays out of the test mailbox: the customer number says who.
            subject = f"[TEST for customer {customer_id}] {subject}"
        draft = self._call(
            "POST",
            f"/users/{self.mailbox}/messages",
            json={
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
        ).json()
        self._call("POST", f"/users/{self.mailbox}/messages/{draft['id']}/send")
        return {
            "delivered": True,
            "external_id": draft["id"],
            "external_conversation_id": draft.get("conversationId"),
        }

    def unread(self) -> list[dict]:
        response = self._call(
            "GET",
            f"/users/{self.mailbox}/mailFolders/inbox/messages",
            params={
                "$filter": "isRead eq false",
                "$top": str(UNREAD_PAGE),
                "$select": "id,subject,body,from,conversationId,receivedDateTime",
                "$orderby": "receivedDateTime",
            },
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        emails = []
        for item in response.json().get("value", []):
            sender = ((item.get("from") or {}).get("emailAddress") or {}).get("address", "")
            emails.append(
                {
                    "id": item["id"],
                    "from": sender.lower(),
                    "subject": item.get("subject") or None,
                    "body": reply_text((item.get("body") or {}).get("content") or ""),
                    "conversation_id": item.get("conversationId"),
                    "received_at": item.get("receivedDateTime"),
                }
            )
        return emails

    def mark_read(self, message_id: str) -> None:
        self._call("PATCH", f"/users/{self.mailbox}/messages/{message_id}", json={"isRead": True})


def build_mailer(settings: Settings, client: httpx.Client | None = None):
    if settings.email_provider == "graph":
        return GraphMailer(settings, client)
    return RecordOnly()
