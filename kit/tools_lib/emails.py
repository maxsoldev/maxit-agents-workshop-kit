"""Read .eml files with the standard `email` package."""

from __future__ import annotations

import email
from email import policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path


class _Text(HTMLParser):
    """Collect the text of an HTML part; skip <script> and <style>."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def body_text(msg) -> str:
    """Plain text of a message. Uses the text/plain part; if there is none, the HTML part as text."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    return html_to_text(text) if part.get_content_type() == "text/html" else text.strip()


def read_eml(path: Path) -> dict:
    """From, date (ISO), subject and plain text of one .eml file."""
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    try:
        date = parsedate_to_datetime(msg["Date"]).isoformat()
    except (TypeError, ValueError):
        date = ""
    return {"from": str(msg["From"] or ""), "date": date, "subject": str(msg["Subject"] or ""), "text": body_text(msg)}
