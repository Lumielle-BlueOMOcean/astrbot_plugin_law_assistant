from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from quart import Quart
from werkzeug.datastructures import FileStorage

from document_ingestion import DocumentIngestionService
from library_repository import LibraryRepository
from library_service import LibraryService
from service import LawAssistantService
from storage import SQLiteStorage
from web_api import LawAssistantWebApi


class RegisteredContext:
    def __init__(self) -> None:
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, handler, methods, desc))


def _app_for(api: LawAssistantWebApi) -> Quart:
    context = RegisteredContext()
    api.register(context)
    app = Quart(__name__)
    for route, handler, methods, _ in context.routes:
        app.add_url_rule(f"/api/plug{route}", view_func=handler, methods=methods)
    return app


def _service(tmp_path: Path) -> LawAssistantService:
    storage = SQLiteStorage(tmp_path / "law.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    ingestion = DocumentIngestionService(tmp_path, library)
    return LawAssistantService(
        storage,
        library_service=library,
        document_ingestion=ingestion,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )


def test_web_api_routes_use_plugin_namespace():
    context = RegisteredContext()
    LawAssistantWebApi(object()).register(context)

    assert context.routes
    assert all(
        route.startswith("/astrbot_plugin_law_assistant/")
        for route, _, _, _ in context.routes
    )
    assert {
        (route, method) for route, _, methods, _ in context.routes for method in methods
    } >= {
        ("/astrbot_plugin_law_assistant/overview", "GET"),
        ("/astrbot_plugin_law_assistant/library/search", "GET"),
        ("/astrbot_plugin_law_assistant/radar/scan", "POST"),
        ("/astrbot_plugin_law_assistant/files/stage", "POST"),
        ("/astrbot_plugin_law_assistant/plans/prepare", "POST"),
    }


@pytest.mark.asyncio
async def test_web_api_registers_overview_and_returns_stable_json(tmp_path):
    service = _service(tmp_path)
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        response = await client.get("/api/plug/astrbot_plugin_law_assistant/overview")
        payload = await response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["schema_version"] == 9
    assert "learning" in payload["data"]
    service.storage.close()


@pytest.mark.asyncio
async def test_web_upload_prepare_confirm_and_one_time_token(tmp_path):
    service = _service(tmp_path)
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        bad = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/files/stage",
            files={
                "file": FileStorage(stream=io.BytesIO(b"x"), filename="../escape.exe")
            },
        )
        assert bad.status_code == 400
        assert (await bad.get_json())["error"] == "unsupported_format"

        staged = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/files/stage",
            files={
                "file": (
                    FileStorage(
                        stream=io.BytesIO(
                            "第1题 单项选择题\n题干\nA. 一\nB. 二\n答案：A\n".encode()
                        ),
                        filename="case.txt",
                    )
                )
            },
        )
        staged_payload = await staged.get_json()
        assert staged_payload["success"] is True
        staged_path = staged_payload["data"]["staged_path"]

        prepared = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/imports/prepare",
            json={"staged_path": staged_path, "content_kind": "mock_question"},
        )
        prepared_payload = await prepared.get_json()
        assert prepared_payload["success"] is True
        token = prepared_payload["data"]["token"]
        assert prepared_payload["data"]["preview"]["total_candidates"] == 1

        confirmed = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/imports/confirm",
            json={"token": token},
        )
        confirmed_payload = await confirmed.get_json()
        assert confirmed_payload["success"] is True
        assert confirmed_payload["data"]["archived"] == 1

        replay = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/imports/confirm",
            json={"token": token},
        )
        assert replay.status_code == 400
        assert (await replay.get_json())["error"] == "invalid_token"

    assert (
        service.storage.connection.execute(
            "SELECT COUNT(*) FROM learning_items"
        ).fetchone()[0]
        == 1
    )
    service.storage.close()


@pytest.mark.asyncio
async def test_web_plan_prepare_does_not_persist_before_confirm(tmp_path):
    service = _service(tmp_path)
    service.storage.bind_target("aiocqhttp:group:100", "一群")
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        prepared = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/plans/prepare",
            json={
                "scope": "target",
                "target": "1",
                "content_type": "daily_question",
                "changes": {
                    "enabled": True,
                    "selection_mode": "rotation",
                    "rotation_subjects": ["intellectual_property", "economic_law"],
                    "rotation_start_date": "2026-09-21",
                },
            },
        )
        payload = await prepared.get_json()
        assert payload["success"] is True
        assert len(payload["data"]["plans"][0]["preview"]) == 14
        assert service.storage.get_daily_plan(1, "daily_question") is None

        confirmed = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/plans/confirm",
            json={"token": payload["data"]["token"]},
        )
        assert (await confirmed.get_json())["success"] is True
        assert service.storage.get_daily_plan(1, "daily_question") is not None

    service.storage.close()


@pytest.mark.asyncio
async def test_web_api_rejects_invalid_library_update(tmp_path):
    service = _service(tmp_path)
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        response = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/library/update",
            json={"item_id": 1, "changes": {"identity": "official_case"}},
        )
        payload = await response.get_json()

    assert response.status_code == 400
    assert payload["success"] is False
    assert payload["error"] == "invalid_update_field"
    service.storage.close()
