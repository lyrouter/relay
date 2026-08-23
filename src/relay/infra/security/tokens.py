"""Single-use secret tokens (AC-1 verification, AC-9 invitations, API-1).

One rule, applied everywhere: **the database stores a hash, the user gets the
plaintext once.** A verification link sitting in a mailbox is a bearer
credential; a database dump should not contain working ones.

SHA-256 rather than Argon2 here, deliberately. These are 256-bit random values,
not human-chosen secrets — there is nothing to brute-force, and a slow KDF on a
hot path (every API request verifies a token) buys nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: 32 bytes, URL-safe. Long enough that a token is never guessed and short
#: enough to survive a mail client wrapping the link.
TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str, token_hash: str) -> bool:
    """Constant-time, so a lookup that compares in Python cannot be timed."""
    return hmac.compare_digest(hash_token(token), token_hash)
