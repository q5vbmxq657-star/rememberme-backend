from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic import model_validator
import psycopg

from app.security.user_auth import AuthenticatedSessionPrincipal, require_authenticated_principal
from app.services.family_repository import FamilyRepository

router = APIRouter()


class FamilyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    display_name: str = Field(min_length=1, max_length=60)


class FamilyJoin(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(min_length=40, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=60)


class FamilyName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class HandoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: UUID


class FamilyContent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    family_id: UUID
    profile_name: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=32000)
    kind: Literal["story", "photo", "video"]
    asset_id: UUID | None = None
    expected_revision: int = Field(strict=True, ge=0, le=9223372036854775806)

    @model_validator(mode="after")
    def content_matches_kind(self):
        if self.kind == "story" and (not self.text or self.asset_id is not None):
            raise ValueError("A story needs text and cannot reference media.")
        if self.kind != "story" and self.asset_id is None:
            raise ValueError("Upload the media before sharing it.")
        return self


class ContentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1, le=9223372036854775806)


class FamilyContentEdit(ContentRevision):
    family_id: UUID
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=32000)


class FamilyCollaborationUpdate(ContentRevision):
    family_id: UUID
    allows_family_edits: bool = Field(strict=True)


def execute(principal, operation, *args):
    try:
        repository = FamilyRepository()
        result = getattr(repository, operation)(principal, *args)
        return JSONResponse(jsonable_encoder(result), headers={"Cache-Control": "no-store"}) if result is not None else Response(status_code=204)
    except psycopg.Error as error:
        raise HTTPException(503, "Family could not be updated. Refresh to check the latest state before trying again.") from error


@router.get("")
def snapshot(principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "snapshot")


@router.get("/content")
def library(after_profile_id: UUID | None = None, after_memory_id: UUID | None = None,
            principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    if (after_profile_id is None) != (after_memory_id is None):
        raise HTTPException(422, "Both cursor fields are required.")
    return execute(principal, "library", after_profile_id, after_memory_id)


@router.get("/credits")
def credits(principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "credits")


@router.put("/content/{profile_id}/{memory_id}")
def publish_content(profile_id: UUID, memory_id: UUID, body: FamilyContent,
                    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "publish_content", profile_id, memory_id, body)


@router.post("/content/{profile_id}/{memory_id}/withdraw")
def withdraw_content(profile_id: UUID, memory_id: UUID, body: ContentRevision,
                     principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "withdraw_content", profile_id, memory_id, body.expected_revision)


@router.get("/content/{profile_id}/{memory_id}")
def content_item(profile_id: UUID, memory_id: UUID,
                 expected_revision: int | None = Query(default=None, ge=1, le=9223372036854775806),
                 principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "content_item", profile_id, memory_id, expected_revision)


@router.patch("/content/{profile_id}/{memory_id}")
def edit_content(profile_id: UUID, memory_id: UUID, body: FamilyContentEdit,
                 principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "edit_content", profile_id, memory_id, body)


@router.patch("/content/{profile_id}/{memory_id}/collaboration")
def update_collaboration(profile_id: UUID, memory_id: UUID, body: FamilyCollaborationUpdate,
                         principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "update_collaboration", profile_id, memory_id, body)


@router.get("/content/{profile_id}/{memory_id}/media")
def content_media(profile_id: UUID, memory_id: UUID,
                  expected_revision: int = Query(ge=1, le=9223372036854775806),
                  principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    try:
        media = FamilyRepository().content_media(principal, profile_id, memory_id, expected_revision)
        return FileResponse(path=media.storage_path, media_type=media.content_type,
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    except psycopg.Error as error:
        raise HTTPException(503, "Shared media is temporarily unavailable.") from error


@router.patch("", status_code=204)
def rename_family(body: FamilyName, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "rename_family", body.name)


@router.patch("/me", status_code=204)
def rename_member(body: FamilyName, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "rename_member", body.name)


@router.post("/handover", status_code=204)
def propose_handover(body: HandoverRequest, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "propose_handover", body.candidate_id)


@router.post("/handover/{transfer_id}/accept", status_code=204)
def accept_handover(transfer_id: UUID, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "resolve_handover", transfer_id, True)


@router.delete("/handover/{transfer_id}", status_code=204)
def cancel_handover(transfer_id: UUID, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "resolve_handover", transfer_id, False)


@router.post("", status_code=204)
def create(body: FamilyCreate, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "create", body.name, body.display_name)


@router.post("/invitations")
def invite(principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "invite")


@router.post("/requests", status_code=204)
def join(body: FamilyJoin, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "claim", body.code, body.display_name)


@router.delete("/requests", status_code=204)
def cancel(principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "cancel_request")


@router.post("/invitations/{invitation_id}/approve", status_code=204)
def approve(invitation_id: UUID, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "resolve_invitation", invitation_id, True)


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke(invitation_id: UUID, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "resolve_invitation", invitation_id, False)


@router.delete("/members/{member_id}", status_code=204)
def remove(member_id: UUID, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "remove", member_id)


@router.delete("", status_code=204)
def close(principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    return execute(principal, "close")
