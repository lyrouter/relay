"""MailPort implementations.

F-5 confirmed a transactional sending path exists, so AC-1's verification email
ships as designed. F-1's in-app-only decision is about *notifications* — a
separate question from verification mail, which does get sent.

``NullMailPort`` (in ``relay.ports.mail``) records instead of sending and is
what tests and an unconfigured deployment use. ``SmtpMailPort`` is the real one;
it reads its settings from the environment so that pointing a deployment at the
relay is configuration, not a code change.
"""

from relay.infra.mail.smtp import SmtpMailPort
from relay.ports.mail import MailPort, NullMailPort

__all__ = ["SmtpMailPort", "build_mail_port"]


def build_mail_port(settings) -> MailPort:
    """Pick a transport from configuration.

    Lives here rather than next to the port: a port declares a contract and may
    not know its implementations. `.importlinter` caught the first attempt to
    put it there, which is the guard doing its job on real code rather than on
    a probe.

    An unconfigured deployment gets ``NullMailPort`` rather than a crash on
    first signup — but it is a *recording* null, so the missing configuration
    surfaces as "the verification mail is in the log, not the inbox" rather
    than as silence.
    """
    if not settings.smtp_host:
        return NullMailPort()
    return SmtpMailPort(
        settings.smtp_host,
        settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        use_starttls=settings.smtp_use_starttls,
        sender=settings.mail_sender,
    )
