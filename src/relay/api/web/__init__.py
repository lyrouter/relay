"""The Web UI's own HTTP surface (``/web``).

Separate from ``/api/v1`` on purpose, and the distinction is a contract decision
rather than tidiness:

* **``/api/v1`` is frozen.** External systems store its field names and enum
  values; a change there goes through §8.6 (additive in v1, breaking means v2)
  and shows up in the committed OpenAPI snapshot.
* **``/web`` ships with the frontend that consumes it.** Both halves live in this
  repository and deploy together, so a field can be renamed in one commit. It is
  versionless because a version number nobody can be out of step with is
  decoration.

What ``/web`` is **not** allowed to be is a second implementation (§8.1). Every
route here calls the same application-layer use case the public API calls; the
only things a router may do are parse, authorize the *transport* (session,
CSRF), and serialize. ``import-linter`` enforces the floor mechanically — no
router may reach the repository layer — and the reason is that the drift it
prevents is invisible in a diff: "changing it in the UI notifies, changing it
through the API doesn't".
"""
