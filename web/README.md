# Relay · S1 frontend

Vue 3 + TypeScript + Vite + Pinia. Covers **LOG-1/2/3/7** (the editor, rendering,
inline ticket cards, templates), **TKT-5/6/7/9** (list, board, my tickets,
detail), and the account screens that consume WEB-2/WEB-4 (signup · verify ·
invite accept · Admin member review).

```bash
make web-install     # from the repository root
make web-types       # regenerate src/api/schema.d.ts from the running app's schema
make web-dev         # vite on :5173
```

`make serve` (the backend, on :8000) has to be running. The dev server **proxies**
`/web`, `/api` and `/blobs` rather than calling them cross-origin — the session
cookie is `HttpOnly` + `SameSite=Lax`, and a cross-origin request would not carry
it at all. Two backend settings make local development work:
`RELAY_SESSION_COOKIE_SECURE=false` (a browser silently drops a `Secure` cookie
over http) and `RELAY_WEB_ORIGINS` including `http://localhost:5173` (the CSRF
check refuses an unrecognised `Origin` on writes).

## The types are generated, and that is the point

`src/api/schema.d.ts` comes from the application's own OpenAPI document via
`openapi-typescript`; `src/api/types.ts` puts readable names on the handful of
shapes the UI uses. So a `/web` response that loses a field **fails the frontend
build** instead of rendering as `undefined` (§8.9). `src/api/schema.json` is
derived and gitignored — regenerate it with `make web-types`, and CI does the same
before building.

`components["schemas"]["relay__api__web__tickets__TicketResponse"]` is FastAPI's
disambiguation: `/web` and `/api/v1` each define a `TicketResponse`. Aliasing them
in one file is what stops a component from accidentally importing the **public
API's** shape while talking to the web surface. The two are allowed to differ —
that is what `/web` being versionless means.

## Four things worth knowing before changing anything here

**`api.patch(..., rev)` is not optional on a ticket.** Every ticket mutation
carries `If-Match: <rev>`, and a 409 means somebody saved first — the store
re-reads and shows a banner rather than retrying. Retrying with a fresh `rev` is
precisely the silent overwrite `rev` exists to prevent. Log saves are different
and deliberately so: they have no `rev`, because LOG-4 answers the same question
with an edit lock plus a version per save (see `stores/logs.ts`).

**`v-html` in `MarkdownView` is safe because of a decision made elsewhere.**
markdown-it runs with `html: false` in `markdown/renderer.ts`, so no
author-supplied markup survives parsing. If that ever changes, this app has a
stored-XSS hole with a human delivery mechanism.

**An unresolved `#331` must stay plain text.** LOG-3: no permission, or no such
ticket, and it renders exactly as typed — no tooltip, no "no access" badge, no
pointer cursor. Any of those would confirm that RL-331 exists, which is the fact
the 404 exists to hide.

**Permissions come from `/web/session`, never from `role === "admin"`.** The
capability list is computed by the backend from the user's *current* role;
re-deriving it here would be a second copy of the permission matrix, and the drift
is invisible until somebody clicks a button that 403s.

## Two cut candidates live here

`views/BoardView.vue` (TKT-6, 2.5 pd) and `markdown/templates.ts` +
its picker (LOG-7, 1 pd) are the plan's cut candidates #2 and #1. Both are
deliberately self-contained: deleting the view and its route, or the templates
module and the buttons that import it, removes the feature without touching
anything else. P-5's recommendation is to keep them and schedule them last.

## No unit tests here, and that is a choice

The frontend's gate is `npm run build` (types + compile), which is what catches
the class of bug this layer actually produces: a shape that no longer matches the
API. Behaviour is covered by the backend suite and by `tests/test_end_to_end.py`,
which drives the same endpoints these stores call. A component test suite is worth
adding when there is component logic worth testing — today the logic is in the
stores and the stores are thin.
