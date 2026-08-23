"""Application layer — command/query orchestration.

Design §8.1: **the web UI and the public API go through these same use cases.**
The API is a contract layer (auth, serialization, idempotency, error shape) on
top, never a parallel implementation. Otherwise state-machine validation,
permission checks and notification triggers drift, and the symptom is "changing
it in the UI notifies, changing it via the API doesn't" — which nobody spots by
reading code. Enforced by `.importlinter`.
"""
