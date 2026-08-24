"""LOG-8 · one search box over logs and ticket titles.

Thin by design: the use case holds the capability check and the S-19 read audit,
and the pgroonga adapter holds the share-level filter. What this route
contributes is the query-string shape — and one refusal that belongs at the
transport edge.

**An empty query returns nothing, not everything.** The adapter already does
that; the route does not paper over it with a default listing. A search box that
returns the corpus on an accidental Enter is how people find things they were not
looking for.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from relay.api.dependencies import Session
from relay.api.wiring import search_port
from relay.app.search import SearchUseCase

router = APIRouter(prefix="/web/search", tags=["search"])


class HitResponse(BaseModel):
    kind: str
    id: uuid.UUID
    title: str
    snippet: str
    #: **Recency, not relevance** — see the adapter. Documented as informational
    #: so nobody builds a "best match" label on top of a timestamp.
    score: float


class SearchResponse(BaseModel):
    hits: list[HitResponse]
    total: int


@router.get("", response_model=SearchResponse)
def search(
    session: Session,
    q: str = "",
    kind: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    results = SearchUseCase(search_port()).execute(q, kinds=tuple(kind or ()), limit=limit)
    return SearchResponse(
        hits=[
            HitResponse(
                kind=one.kind, id=one.id, title=one.title, snippet=one.snippet, score=one.score
            )
            for one in results.hits
        ],
        total=results.total,
    )
