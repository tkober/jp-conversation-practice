"""Scenario CRUD, seeding and the customisation guard."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from app import db
from app.scenario_files import load_scenario_files


async def test_builtin_scenarios_are_seeded_from_markdown(api: AsyncClient) -> None:
    body = (await api.get("/api/scenarios")).json()

    slugs = {row["slug"] for row in body}
    assert {file.slug for file in load_scenario_files()} <= slugs
    assert all(row["is_builtin"] for row in body if row["slug"] == "konbini")


async def test_scenarios_keep_the_file_order(api: AsyncClient) -> None:
    body = (await api.get("/api/scenarios")).json()

    builtin = [row["slug"] for row in body if row["is_builtin"]]
    assert builtin[: len(load_scenario_files())] == [
        file.slug for file in load_scenario_files()
    ]


async def test_creating_a_scenario_derives_a_slug(api: AsyncClient) -> None:
    body = (
        await api.post(
            "/api/scenarios",
            json={"title": "Beim Friseur!", "summary": "Haare schneiden", "prompt": "You are a hairdresser."},
        )
    ).json()

    assert body["slug"] == "beim-friseur"
    assert body["is_builtin"] is False
    assert body["is_customized"] is True


async def test_duplicate_titles_get_distinct_slugs(api: AsyncClient) -> None:
    payload = {"title": "Beim Friseur", "prompt": "You are a hairdresser."}
    first = (await api.post("/api/scenarios", json=payload)).json()
    second = (await api.post("/api/scenarios", json=payload)).json()

    assert first["slug"] != second["slug"]
    assert second["slug"].startswith(first["slug"])


async def test_editing_a_builtin_marks_it_customised(api: AsyncClient) -> None:
    scenarios = (await api.get("/api/scenarios")).json()
    konbini = next(row for row in scenarios if row["slug"] == "konbini")

    body = (
        await api.put(
            f"/api/scenarios/{konbini['id']}", json={"prompt": "You are a vending machine."}
        )
    ).json()

    assert body["prompt"] == "You are a vending machine."
    assert body["is_customized"] is True


async def test_seeding_does_not_overwrite_a_customised_scenario(api: AsyncClient) -> None:
    """The guard that makes editing built-in scenarios safe across redeploys."""
    scenarios = (await api.get("/api/scenarios")).json()
    konbini = next(row for row in scenarios if row["slug"] == "konbini")
    await api.put(f"/api/scenarios/{konbini['id']}", json={"prompt": "Mine, not the file's."})

    async with db.get_sessionmaker()() as session:
        await db.seed_scenarios(session)
        await session.commit()

    body = (await api.get("/api/scenarios")).json()
    assert next(row for row in body if row["slug"] == "konbini")["prompt"] == (
        "Mine, not the file's."
    )


async def test_reset_restores_the_file_version(api: AsyncClient) -> None:
    scenarios = (await api.get("/api/scenarios")).json()
    konbini = next(row for row in scenarios if row["slug"] == "konbini")
    await api.put(f"/api/scenarios/{konbini['id']}", json={"prompt": "Temporary."})

    body = (await api.post(f"/api/scenarios/{konbini['id']}/reset")).json()

    original = next(f for f in load_scenario_files() if f.slug == "konbini")
    assert body["prompt"] == original.prompt
    assert body["is_customized"] is False


async def test_a_custom_scenario_cannot_be_reset(api: AsyncClient) -> None:
    created = (
        await api.post("/api/scenarios", json={"title": "Eigenes", "prompt": "You are you."})
    ).json()

    response = await api.post(f"/api/scenarios/{created['id']}/reset")

    assert response.status_code == 400


async def test_deleting_a_scenario_removes_it(api: AsyncClient) -> None:
    created = (
        await api.post("/api/scenarios", json={"title": "Weg damit", "prompt": "You are gone."})
    ).json()

    assert (await api.delete(f"/api/scenarios/{created['id']}")).status_code == 204

    slugs = {row["slug"] for row in (await api.get("/api/scenarios")).json()}
    assert created["slug"] not in slugs


async def test_missing_scenario_is_a_404(api: AsyncClient) -> None:
    assert (await api.put("/api/scenarios/999999", json={"title": "x"})).status_code == 404
    assert (await api.delete("/api/scenarios/999999")).status_code == 404
