"""Runtime settings.

Three separate DSNs on purpose (design §2.4, RLS details 1 and 3):

* ``owner_dsn``   — migrations only. Owns the tables, so RLS would not bind it
                    even with FORCE; that is exactly why the app never uses it.
* ``app_dsn``     — the runtime role. Non-owner, NOBYPASSRLS.
* ``system_dsn``  — BYPASSRLS, for ``SystemRepository`` only, audited per call.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELAY_", env_file=".env", extra="ignore")

    pg_host: str = "127.0.0.1"
    pg_port: int = 5433
    pg_database: str = "relay_dev"

    owner_user: str = "relay_owner"
    owner_password: str = "relay_owner"
    app_user: str = "relay_app"
    app_password: str = "relay_app"
    system_user: str = "relay_system"
    system_password: str = "relay_system"

    sql_echo: bool = False

    #: Where verification links point. Carries the tenant segment (S-12), so it
    #: is the base only — the slug is appended per tenant.
    public_base_url: str = "https://relay.internal"

    #: Mail. Empty host means NullMailPort: messages are recorded, not sent.
    #: F-5 confirmed a real sending path exists; this is where it gets pointed.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_starttls: bool = True
    mail_sender: str = "relay@relay.internal"

    #: LOG-5 · attachments. The carrier is self-hosted MinIO (F-4); ``blob_root``
    #: is the filesystem implementation used in development and tests, and it is
    #: the same key layout, so switching carriers does not move any object.
    blob_root: str = "./var/blobs"
    #: Signs the short-lived download links. **Must be set in any deployment** —
    #: the default is deliberately obvious so an unset value is visible in a
    #: config review rather than hidden behind a working link.
    blob_signing_key: str = "dev-only-unsafe-signing-key"
    #: S-11: access is permission-checked, then served by a short-lived signed
    #: link. Never "the URL is unguessable".
    blob_link_ttl_seconds: int = 300
    #: 25 MiB. Big enough for a screenshot or a heap dump excerpt, small enough
    #: that INT-11's restore drill stays a drill.
    blob_max_bytes: int = 25 * 1024 * 1024

    def _dsn(self, user: str, password: str) -> str:
        host = self.pg_host
        if host.startswith("/"):
            return (
                f"postgresql+psycopg://{user}:{password}@/{self.pg_database}"
                f"?host={host}&port={self.pg_port}"
            )
        return f"postgresql+psycopg://{user}:{password}@{host}:{self.pg_port}/{self.pg_database}"

    @property
    def owner_dsn(self) -> str:
        return self._dsn(self.owner_user, self.owner_password)

    @property
    def app_dsn(self) -> str:
        return self._dsn(self.app_user, self.app_password)

    @property
    def system_dsn(self) -> str:
        return self._dsn(self.system_user, self.system_password)


settings = Settings()
