"""Runtime settings.

Three separate DSNs on purpose (design §2.4, RLS details 1 and 3):

* ``owner_dsn``   — migrations only. Owns the tables, so RLS would not bind it
                    even with FORCE; that is exactly why the app never uses it.
* ``app_dsn``     — the runtime role. Non-owner, NOBYPASSRLS.
* ``system_dsn``  — BYPASSRLS, for ``SystemRepository`` only, audited per call.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


def _origin_of(url: str) -> str:
    """Scheme and authority only — what a browser puts in ``Origin``."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme else url.rstrip("/")


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

    #: The web surface (WEB-1). Session cookie hardening and the CSRF origin
    #: allowlist. Defaults are the safe ones, which means **local http
    #: development has to opt out of Secure** (``RELAY_SESSION_COOKIE_SECURE=false``)
    #: — a browser silently drops a Secure cookie on http, and "login does
    #: nothing" is a bad first hour.
    session_cookie_secure: bool = True
    #: Comma-separated origins allowed to make state-changing requests. Empty
    #: means "just the public base URL's own origin", which is right in
    #: production and wrong for a Vite dev server on another port — add it here
    #: rather than turning the check off.
    web_origins: str = ""
    #: Comma-separated addresses of proxies whose ``X-Forwarded-For`` may be
    #: believed. **Empty means believe nobody**, so the client address is the
    #: peer address. Signup and login throttles are per IP (AC-1 / AC-2); a
    #: forwarded header trusted by default lets one caller spend everybody
    #: else's attempts, or dodge their own limit with a new header value.
    trusted_proxies: str = ""

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

    #: S-25 · which carrier serves attachments. ``filesystem`` is development
    #: and test; ``minio`` is the deployed one (F-4). **The switch is here and
    #: nowhere else**, because it changes more than a class: with ``minio`` the
    #: signed link points at the object store and the ``/blobs/{key}`` route
    #: stops existing (it needs the filesystem carrier's ``verify``/``open``,
    #: which S3 has no equivalent of). A carrier chosen implicitly per call site
    #: would leave that route mounted and answering ``AttributeError``.
    blob_carrier: str = "filesystem"
    #: The endpoint the **application** connects to, typically an internal
    #: address: ``http://minio.internal:9000``.
    minio_endpoint: str = ""
    #: The endpoint that goes into a **signed URL**, i.e. the one the browser
    #: resolves. Separate from the above on purpose — this is blind spot #1 in
    #: the S-25 write-up: MinIO is normally deployed as "internal endpoint +
    #: reverse-proxied public name", and a link signed for the internal host is
    #: a broken image with nothing in the application log, because the browser
    #: talks to the object store directly and never comes back here. Empty means
    #: "same as ``minio_endpoint``", which is right only when they really are.
    minio_public_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "relay-attachments"
    minio_region: str = "us-east-1"
    #: MinIO is addressed **path-style** (``endpoint/bucket/key``). Configurable
    #: only so the same adapter can point at a real S3, and defaulted the way the
    #: S1 carrier needs — an SDK default of virtual-host addressing turns into a
    #: ``NoSuchBucket`` or a DNS failure resolving the bucket as a subdomain
    #: (blind spot #2).
    minio_path_style: bool = True

    #: API-4 · the master key every webhook endpoint's signing secret is derived
    #: from (``relay.app.webhooks.secret_for``). **Must be set per environment**,
    #: and — unlike the blob key — **changing it breaks every consumer**: their
    #: stored secret stops matching the signature we send. So it is not a value to
    #: rotate casually; rotate a single endpoint instead. Same obvious default as
    #: the blob key so that an unset value is visible in a config review.
    webhook_signing_key: str = "dev-only-unsafe-webhook-key"

    @property
    def minio_signing_endpoint(self) -> str:
        """The endpoint a presigned URL is built against. See the two above."""
        return self.minio_public_endpoint or self.minio_endpoint

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Origins accepted on a state-changing request. See ``web_origins``."""
        configured = tuple(
            one.strip().rstrip("/") for one in self.web_origins.split(",") if one.strip()
        )
        return configured or (_origin_of(self.public_base_url),)

    @property
    def trusted_proxy_addresses(self) -> frozenset[str]:
        return frozenset(
            one.strip() for one in self.trusted_proxies.split(",") if one.strip()
        )

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
