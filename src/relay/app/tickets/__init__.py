"""Tickets: entity operations, the state machine, board metadata (TKT).

§8.1: the web UI and the public API go through **these** use cases. The API adds
auth, serialization, idempotency and error shape on top; it does not reimplement
validation, the state machine, or the notification triggers. Otherwise the two
drift and the symptom — "changing it in the UI notifies, changing it via the API
doesn't" — is invisible in a diff.
"""
