"""AC-5 · spaces, which are how L2 sharing gets a meaning.

Creating a space and adding somebody to it **is** granting L2 read access to
whatever it holds, which is why ``SPACE_MANAGE`` sits with the other
access-granting powers rather than with "edit your own log" — and why membership
changes are audited.

One rule the endpoints inherit and the UI has to respect: **a Guest may be added
to a space and still does not reach L2** (S-6). The role is checked before the
membership, so "add the contractor to the team space" cannot quietly hand over
every log shared into it. The membership is a convenience for organising people;
the reach is a role decision.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.app.accounts.spaces import SpaceService
from relay.domain.enums import SpaceRole

router = APIRouter(prefix="/web/spaces", tags=["spaces"])


class CreateSpacePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class CreatedSpaceResponse(BaseModel):
    id: uuid.UUID


class AddMemberPayload(BaseModel):
    user_id: uuid.UUID
    space_role: SpaceRole = SpaceRole.MEMBER


class MembersResponse(BaseModel):
    member_ids: list[uuid.UUID]


@router.post("", response_model=CreatedSpaceResponse, status_code=status.HTTP_201_CREATED)
def create_space(payload: CreateSpacePayload, session: Session) -> CreatedSpaceResponse:
    """The creator becomes the first owner — not a separate step, because a space
    with no owner is one only an Admin can change."""
    return CreatedSpaceResponse(id=SpaceService().create(payload.name, payload.description))


@router.get("/mine", response_model=list[uuid.UUID])
def my_spaces(session: Session) -> list[uuid.UUID]:
    """Which spaces the caller is in — what the share-level picker offers for L2."""
    return sorted(SpaceService().space_ids_for(session.user_id))


@router.get("/{space_id}/members", response_model=MembersResponse)
def space_members(space_id: uuid.UUID, session: Session) -> MembersResponse:
    return MembersResponse(member_ids=SpaceService().member_ids(space_id))


@router.post("/{space_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(space_id: uuid.UUID, payload: AddMemberPayload, session: Session) -> Response:
    SpaceService().add_member(space_id, payload.user_id, payload.space_role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{space_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(space_id: uuid.UUID, user_id: uuid.UUID, session: Session) -> Response:
    """Removing the last owner is refused, and removing anyone does **not** touch
    their L1 grants: losing L2 is losing the space, not losing what was shared
    with you by name."""
    SpaceService().remove_member(space_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
