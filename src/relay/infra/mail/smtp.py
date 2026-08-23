"""SMTP transport for MailPort.

Deliberately small. Everything about *what* to send lives in the use cases; this
knows only how to hand a message to a relay.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from relay.ports.mail import OutboundMail


class SmtpMailPort:
    def __init__(
        self,
        host: str,
        port: int = 587,
        *,
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
        sender: str = "relay@relay.internal",
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_starttls = use_starttls
        self._sender = sender
        self._timeout = timeout

    def send(self, mail: OutboundMail) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message.set_content(mail.text_body)
        if mail.html_body:
            message.add_alternative(mail.html_body, subtype="html")

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            if self._use_starttls:
                client.starttls(context=ssl.create_default_context())
            if self._username:
                client.login(self._username, self._password or "")
            client.send_message(message)
