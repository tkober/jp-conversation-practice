"""Context material: upload, evaluation, editing and the prompt block."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api import attachments
from app.context_material import ContextMaterialError, MaterialAnalysis
from app.models import ContextItem
from app.prompts import build_realtime_instructions, format_context_block

# A one-pixel PNG: the bytes only have to be a plausible image, since the
# evaluation that would look at them is stubbed out.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "000557bfabd40000000049454e44ae426082"
)


class _StubService:
    """Stands in for the vision call. `error` makes every call fail."""

    error: str | None = None
    calls: list[str] = []

    def __init__(self, config: object) -> None:
        self.config = config

    async def describe_image(self, **kwargs: object) -> MaterialAnalysis:
        return self._result("image", kwargs)

    async def describe_text(self, **kwargs: object) -> MaterialAnalysis:
        return self._result("text", kwargs)

    def _result(self, kind: str, kwargs: dict[str, object]) -> MaterialAnalysis:
        type(self).calls.append(str(kwargs.get("scenario_prompt", "")))
        if type(self).error:
            raise ContextMaterialError(type(self).error)
        return MaterialAnalysis(
            title="Speisekarte des Izakaya",
            description=f"A {kind} menu: 唐揚げ (からあげ) – 600円.",
        )


@pytest.fixture(autouse=True)
def stub_evaluation(monkeypatch: pytest.MonkeyPatch) -> type[_StubService]:
    _StubService.error = None
    _StubService.calls = []
    monkeypatch.setattr(attachments, "ContextMaterialService", _StubService)
    return _StubService


async def scenario_id(api: AsyncClient) -> int:
    body = (
        await api.post(
            "/api/scenarios",
            json={"title": "Izakaya", "prompt": "You are a waiter in an izakaya."},
        )
    ).json()
    return int(body["id"])


async def upload(api: AsyncClient, scenario: int, **form: object) -> dict:
    response = await api.post(
        f"/api/scenarios/{scenario}/attachments/image",
        files={"file": ("menu.png", PNG, "image/png")},
        data={"title": "", "hint": "", "available_from_start": "true", **form},
    )
    return response.json() | {"_status": response.status_code}


# --- upload and evaluation -------------------------------------------------


async def test_an_uploaded_image_is_described_for_the_tutor(api: AsyncClient) -> None:
    scenario = await scenario_id(api)

    body = await upload(api, scenario)

    assert body["_status"] == 201
    assert body["kind"] == "image"
    assert "唐揚げ" in body["description"]
    # The title comes from the evaluation when the user did not type one.
    assert body["title"] == "Speisekarte des Izakaya"
    assert body["byte_size"] == len(PNG)
    assert body["analysis_error"] is None


async def test_the_evaluation_sees_the_scenario_it_belongs_to(
    api: AsyncClient, stub_evaluation: type[_StubService]
) -> None:
    scenario = await scenario_id(api)

    await upload(api, scenario)

    # A shelf photo reads differently in a konbini than in a supermarket, so
    # the role the material belongs to travels with it.
    assert stub_evaluation.calls == ["You are a waiter in an izakaya."]


async def test_a_typed_title_survives_the_evaluation(api: AsyncClient) -> None:
    scenario = await scenario_id(api)

    body = await upload(api, scenario, title="Meine Karte")

    assert body["title"] == "Meine Karte"


async def test_a_failed_evaluation_keeps_the_upload(
    api: AsyncClient, stub_evaluation: type[_StubService]
) -> None:
    stub_evaluation.error = "the model has no vision support"
    scenario = await scenario_id(api)

    body = await upload(api, scenario)

    # Losing the file would mean finding the photo again; the description is
    # an ordinary editable field, so an empty one is recoverable.
    assert body["_status"] == 201
    assert body["description"] == ""
    assert "vision" in body["analysis_error"]
    assert len((await api.get(f"/api/scenarios/{scenario}/attachments")).json()) == 1


async def test_unevaluated_text_falls_back_to_its_own_body(
    api: AsyncClient, stub_evaluation: type[_StubService]
) -> None:
    stub_evaluation.error = "nope"
    scenario = await scenario_id(api)

    body = (
        await api.post(
            f"/api/scenarios/{scenario}/attachments/text",
            json={"body": "唐揚げ 600円"},
        )
    ).json()

    # Unlike an image, raw text is still readable by the tutor -- it just has
    # not been prepared.
    assert body["description"] == "唐揚げ 600円"


async def test_a_non_image_upload_is_rejected(api: AsyncClient) -> None:
    scenario = await scenario_id(api)

    response = await api.post(
        f"/api/scenarios/{scenario}/attachments/image",
        files={"file": ("menu.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 415
    assert "image/png" in response.json()["detail"]


async def test_an_oversized_image_is_rejected(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "attachment_max_bytes", 10)
    scenario = await scenario_id(api)

    response = await api.post(
        f"/api/scenarios/{scenario}/attachments/image",
        files={"file": ("menu.png", PNG, "image/png")},
    )

    assert response.status_code == 413


# --- serving, editing, deleting -------------------------------------------


async def test_the_image_itself_is_served_back(api: AsyncClient) -> None:
    scenario = await scenario_id(api)
    body = await upload(api, scenario)

    response = await api.get(f"/api/attachments/{body['id']}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG


async def test_a_text_attachment_has_no_file(api: AsyncClient) -> None:
    scenario = await scenario_id(api)
    body = (
        await api.post(
            f"/api/scenarios/{scenario}/attachments/text", json={"body": "唐揚げ 600円"}
        )
    ).json()

    assert (await api.get(f"/api/attachments/{body['id']}/file")).status_code == 404


async def test_the_description_can_be_corrected_by_hand(api: AsyncClient) -> None:
    scenario = await scenario_id(api)
    body = await upload(api, scenario)

    updated = (
        await api.put(
            f"/api/attachments/{body['id']}",
            json={"description": "唐揚げ (からあげ) – 650円, not 600.", "available_from_start": False},
        )
    ).json()

    assert updated["description"].endswith("not 600.")
    assert updated["available_from_start"] is False


async def test_deleting_the_scenario_takes_its_material_with_it(api: AsyncClient) -> None:
    scenario = await scenario_id(api)
    body = await upload(api, scenario)

    await api.delete(f"/api/scenarios/{scenario}")

    # Material has no meaning without the scenario it describes, unlike a
    # session, which records something that happened.
    assert (await api.get(f"/api/attachments/{body['id']}/file")).status_code == 404


async def test_material_for_an_unknown_scenario_is_a_404(api: AsyncClient) -> None:
    response = await api.get("/api/scenarios/999999/attachments")

    assert response.status_code == 404


# --- the prompt block ------------------------------------------------------


def test_a_session_without_material_gets_the_prompt_it_always_got() -> None:
    with_empty = build_realtime_instructions("Kombini", "N5", [])
    without = build_realtime_instructions("Kombini", "N5")

    assert with_empty == without
    assert "# Context material" not in without


def test_material_without_a_description_is_not_offered_to_the_tutor() -> None:
    # An upload whose evaluation failed. Announcing a menu and then saying
    # nothing about it is worse than not mentioning it.
    block = format_context_block([ContextItem(id=1, title="Speisekarte", description="  ")])

    assert block == ""


def test_the_block_names_what_the_learner_can_see() -> None:
    prompt = build_realtime_instructions(
        "You are a waiter.",
        "N4",
        [ContextItem(id=1, title="Speisekarte", description="唐揚げ – 600円")],
    )

    assert "# Context material" in prompt
    assert "## Speisekarte" in prompt
    assert "唐揚げ – 600円" in prompt
    # The two rules the whole feature rests on: the learner sees it, and it is
    # not a running order.
    assert "The learner is looking at this material while you talk" in prompt
    assert "not a plan for the conversation" in prompt


def test_material_handed_over_later_says_so() -> None:
    prompt = build_realtime_instructions(
        "You are a waiter.",
        "N4",
        [ContextItem(id=1, title="Speisekarte", description="x", introduced_at=12.5)],
    )

    assert "## Speisekarte (handed to the learner during the conversation)" in prompt
