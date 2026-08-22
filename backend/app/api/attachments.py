"""Context material: upload, evaluate, edit and serve one scenario's material.

Uploading and evaluating are one request on purpose. The material is only
useful to the tutor once it has been described (see
:mod:`app.context_material`), and an attachment sitting there undescribed is a
scenario that quietly practises without it. Doing both here means the user
picks a file and gets something usable, or gets told why not.

What is *not* one request is failing: an evaluation that goes wrong keeps the
attachment and reports ``analysis_error``, because losing the upload would mean
finding the photo again. The description can then be retried or simply written
by hand -- it is an ordinary editable field.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..context_material import (
    SUPPORTED_IMAGE_TYPES,
    ContextMaterialError,
    ContextMaterialService,
    MaterialAnalysis,
)
from ..db import Scenario, ScenarioAttachment, load_scenario_attachments
from ..models import AttachmentUpdate, AttachmentView, TextAttachmentCreate
from ..runtime_config import RuntimeConfig
from .deps import db_session, runtime_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["attachments"])


def to_view(row: ScenarioAttachment, analysis_error: str | None = None) -> AttachmentView:
    return AttachmentView(
        id=row.id,
        scenario_id=row.scenario_id,
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        description=row.description,
        body=row.body,
        media_type=row.media_type,
        byte_size=len(row.data or b""),
        available_from_start=row.available_from_start,
        sort_order=row.sort_order,
        analysis_error=analysis_error,
    )


async def scenario_or_404(session: AsyncSession, scenario_id: int) -> Scenario:
    row = await session.get(Scenario, scenario_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return row


async def attachment_or_404(session: AsyncSession, attachment_id: int) -> ScenarioAttachment:
    row = await session.get(ScenarioAttachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return row


async def _next_sort_order(session: AsyncSession, scenario_id: int) -> int:
    highest = await session.scalar(
        select(func.max(ScenarioAttachment.sort_order)).where(
            ScenarioAttachment.scenario_id == scenario_id
        )
    )
    return (highest or 0) + 1


async def _evaluate(
    config: RuntimeConfig,
    scenario: Scenario,
    *,
    hint: str,
    media_type: str = "",
    data: bytes | None = None,
    body: str = "",
) -> tuple[MaterialAnalysis | None, str | None]:
    """Describe the material, or explain why it could not be described."""
    service = ContextMaterialService(config)
    try:
        if data is not None:
            result = await service.describe_image(
                media_type=media_type,
                data=data,
                scenario_prompt=scenario.prompt,
                hint=hint,
            )
        else:
            result = await service.describe_text(
                body=body, scenario_prompt=scenario.prompt, hint=hint
            )
    except ContextMaterialError as exc:
        logger.warning("Could not evaluate material for scenario %s: %s", scenario.id, exc)
        return None, str(exc)
    return result, None


# --- nested under the scenario that owns the material ----------------------


@router.get("/api/scenarios/{scenario_id}/attachments", response_model=list[AttachmentView])
async def list_attachments(
    scenario_id: int, session: AsyncSession = Depends(db_session)
) -> list[AttachmentView]:
    await scenario_or_404(session, scenario_id)
    return [to_view(row) for row in await load_scenario_attachments(session, scenario_id)]


@router.post(
    "/api/scenarios/{scenario_id}/attachments/image",
    response_model=AttachmentView,
    status_code=201,
)
async def upload_image(
    scenario_id: int,
    file: UploadFile = File(...),
    title: str = Form(""),
    hint: str = Form(""),
    available_from_start: bool = Form(True),
    session: AsyncSession = Depends(db_session),
    config: RuntimeConfig = Depends(runtime_config),
) -> AttachmentView:
    """Store an image and describe it for the tutor in the same request."""
    media_type = (file.content_type or "").split(";")[0].strip().lower()
    if media_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported image type {media_type or 'unknown'}. Use one of: "
                + ", ".join(sorted(SUPPORTED_IMAGE_TYPES))
            ),
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > config.attachment_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The image is {len(data) // 1024} KB; the limit is "
                f"{config.attachment_max_bytes // 1024} KB."
            ),
        )

    scenario = await scenario_or_404(session, scenario_id)
    analysis, error = await _evaluate(
        config, scenario, hint=hint, media_type=media_type, data=data
    )

    row = ScenarioAttachment(
        scenario_id=scenario_id,
        kind="image",
        title=(title.strip() or (analysis.title if analysis else ""))[:120],
        description=analysis.description if analysis else "",
        media_type=media_type,
        data=data,
        available_from_start=available_from_start,
        sort_order=await _next_sort_order(session, scenario_id),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return to_view(row, error)


@router.post(
    "/api/scenarios/{scenario_id}/attachments/text",
    response_model=AttachmentView,
    status_code=201,
)
async def add_text(
    scenario_id: int,
    payload: TextAttachmentCreate,
    session: AsyncSession = Depends(db_session),
    config: RuntimeConfig = Depends(runtime_config),
) -> AttachmentView:
    """Add a pasted text and describe it for the tutor.

    Text goes through the same evaluation as an image rather than straight into
    the prompt: a menu typed out by hand is still a list, and still has to
    reach the tutor as facts about the scene instead of as a running order.
    """
    body = payload.body.strip()
    if len(body) > config.attachment_max_text_chars:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The text is {len(body)} characters; the limit is "
                f"{config.attachment_max_text_chars}."
            ),
        )

    scenario = await scenario_or_404(session, scenario_id)
    analysis, error = await _evaluate(config, scenario, hint=payload.hint, body=body)

    row = ScenarioAttachment(
        scenario_id=scenario_id,
        kind="text",
        title=(payload.title.strip() or (analysis.title if analysis else ""))[:120],
        # Without an evaluation the raw text is still better than nothing: the
        # tutor can read it, it just has not been prepared. An image has no
        # such fallback, which is why only this branch has one.
        description=analysis.description if analysis else body,
        body=body,
        available_from_start=payload.available_from_start,
        sort_order=await _next_sort_order(session, scenario_id),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return to_view(row, error)


# --- one attachment --------------------------------------------------------


@router.get("/api/attachments/{attachment_id}/file")
async def read_attachment_file(
    attachment_id: int, session: AsyncSession = Depends(db_session)
) -> Response:
    """The image itself, for the panel the learner looks at during a session."""
    row = await attachment_or_404(session, attachment_id)
    if not row.data:
        raise HTTPException(status_code=404, detail="This attachment has no image.")
    return Response(
        content=row.data,
        media_type=row.media_type or "application/octet-stream",
        # The bytes never change once uploaded -- an edit only touches the
        # labels -- so the browser may hold on to them for the whole session.
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.put("/api/attachments/{attachment_id}", response_model=AttachmentView)
async def update_attachment(
    attachment_id: int,
    payload: AttachmentUpdate,
    session: AsyncSession = Depends(db_session),
    config: RuntimeConfig = Depends(runtime_config),
) -> AttachmentView:
    """Edit the labels. The description is an ordinary field on purpose.

    The evaluation is a first draft, not an oracle: a misread price is easier
    to correct here than to argue with a model about.
    """
    row = await attachment_or_404(session, attachment_id)
    provided = payload.model_dump(exclude_unset=True)

    values: dict[str, object] = {"updated_at": func.now()}
    if provided.get("title") is not None:
        values["title"] = str(provided["title"]).strip()[:120]
    if provided.get("description") is not None:
        values["description"] = str(provided["description"]).strip()[
            : config.attachment_description_max_chars
        ]
    if provided.get("available_from_start") is not None:
        values["available_from_start"] = bool(provided["available_from_start"])
    if provided.get("sort_order") is not None:
        values["sort_order"] = int(provided["sort_order"])

    await session.execute(
        update(ScenarioAttachment)
        .where(ScenarioAttachment.id == row.id)
        .values(**values)
    )
    await session.commit()
    return to_view(await attachment_or_404(session, attachment_id))


@router.post("/api/attachments/{attachment_id}/evaluate", response_model=AttachmentView)
async def evaluate_attachment(
    attachment_id: int,
    session: AsyncSession = Depends(db_session),
    config: RuntimeConfig = Depends(runtime_config),
) -> AttachmentView:
    """Run the evaluation again, replacing the description.

    Needed after an upload whose evaluation failed, and useful after editing
    the scenario prompt -- the description is written for a particular role,
    and a shelf photo reads differently in a konbini than in a supermarket.
    """
    row = await attachment_or_404(session, attachment_id)
    scenario = await scenario_or_404(session, row.scenario_id)

    analysis, error = await _evaluate(
        config,
        scenario,
        hint="",
        media_type=row.media_type,
        data=row.data if row.kind == "image" else None,
        body=row.body,
    )
    if analysis is None:
        return to_view(row, error)

    await session.execute(
        update(ScenarioAttachment)
        .where(ScenarioAttachment.id == row.id)
        .values(
            description=analysis.description,
            title=row.title or analysis.title[:120],
            updated_at=func.now(),
        )
    )
    await session.commit()
    return to_view(await attachment_or_404(session, attachment_id))


@router.delete("/api/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: int, session: AsyncSession = Depends(db_session)
) -> None:
    await attachment_or_404(session, attachment_id)
    await session.execute(
        delete(ScenarioAttachment).where(ScenarioAttachment.id == attachment_id)
    )
    await session.commit()
