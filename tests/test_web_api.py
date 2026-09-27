from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from quart import Quart
from werkzeug.datastructures import FileStorage

from daily_plans import DailyPlan
from document_ingestion import DocumentIngestionService
from library_models import LibrarySource
from library_repository import LibraryRepository
from library_service import LibraryService
from service import LawAssistantService
from storage import SQLiteStorage
from structured_ingestion import StructuredMaterialIngestionService
from tests.test_structured_ingestion import _payload, _text_pdf_bytes
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
    structured_ingestion = StructuredMaterialIngestionService(tmp_path, library)
    return LawAssistantService(
        storage,
        library_service=library,
        document_ingestion=ingestion,
        structured_ingestion=structured_ingestion,
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
        ("/astrbot_plugin_law_assistant/structured/stage-json", "POST"),
        ("/astrbot_plugin_law_assistant/structured/prepare", "POST"),
        ("/astrbot_plugin_law_assistant/structured/confirm", "POST"),
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
    assert payload["data"]["schema_version"] == 12
    assert payload["data"]["plugin"]["version"] == "0.5.2"
    assert "learning" in payload["data"]
    service.storage.close()


@pytest.mark.asyncio
async def test_web_api_structured_prepare_and_confirm_uses_two_hash_bound_files(
    tmp_path,
):
    service = _service(tmp_path)
    app = _app_for(LawAssistantWebApi(service))
    pdf_bytes = _text_pdf_bytes()
    payload = _payload(pdf_bytes)

    async with app.test_client() as client:
        staged_pdf = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/files/stage",
            files={
                "file": FileStorage(
                    stream=io.BytesIO(pdf_bytes), filename="verified.pdf"
                )
            },
        )
        pdf_data = (await staged_pdf.get_json())["data"]
        staged_json = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/structured/stage-json",
            files={
                "file": FileStorage(
                    stream=io.BytesIO(json.dumps(payload, ensure_ascii=False).encode()),
                    filename="verified material.json",
                )
            },
        )
        json_data = (await staged_json.get_json())["data"]
        prepared = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/structured/prepare",
            json={
                "original_staged_path": pdf_data["staged_path"],
                "structured_staged_path": json_data["staged_path"],
                "original_filename": "verified.pdf",
                "structured_filename": "verified material.json",
            },
        )
        prepared_payload = await prepared.get_json()
        assert prepared_payload["success"] is True
        assert prepared_payload["data"]["preview"]["counts"]["processable"] == 2
        assert (
            service.storage.connection.execute(
                "SELECT COUNT(*) FROM structured_imports"
            ).fetchone()[0]
            == 0
        )

        confirmed = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/structured/confirm",
            json={"token": prepared_payload["data"]["token"]},
        )
        confirmed_payload = await confirmed.get_json()
        assert confirmed_payload["success"] is True
        assert confirmed_payload["data"]["archived"] == 2

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
async def test_web_plan_reset_preview_returns_current_and_global_plans(tmp_path):
    service = _service(tmp_path)
    target = await service.bind_target("aiocqhttp:group:100", "法硕一群")
    service.storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {
                "enabled": True,
                "selection_mode": "rotation",
                "rotation_subjects": ["intellectual_property", "civil_commercial"],
                "rotation_start_date": "2026-09-21",
            },
        )
    )
    service.storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {
                "enabled": True,
                "selection_mode": "fixed",
                "fixed_subject": "criminal_law",
            },
        ),
        target["id"],
    )
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        response = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/plans/reset-prepare",
            json={"target": "1", "content_type": "daily_question"},
        )
        payload = await response.get_json()

    assert payload["success"] is True
    data = payload["data"]
    assert data["target"]["id"] == target["id"]
    assert data["current_plans"][0]["plan"]["fixed_subject"] == "criminal_law"
    assert data["restored_plans"][0]["plan"]["selection_mode"] == "rotation"
    assert len(data["restored_plans"][0]["preview"]) == 14
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


@pytest.mark.asyncio
async def test_web_library_question_detail_and_update_are_answer_safe(tmp_path):
    service = _service(tmp_path)
    archived = await service.library_service.archive_learning_material(
        raw_text="SECRET_RAW_SOURCE SECRET_ANSWER SECRET_EXPLANATION",
        material_type="mock_question",
        title="Web 安全合成题",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "short_answer",
                "stem": "SAFE_WEB_STEM",
                "options": [],
                "answer": "SECRET_ANSWER",
                "explanation": "SECRET_EXPLANATION",
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="private:42",
    )
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        detail_response = await client.get(
            "/api/plug/astrbot_plugin_law_assistant/library/item",
            query_string={"id": archived["item_id"]},
        )
        detail_payload = await detail_response.get_json()
        update_response = await client.post(
            "/api/plug/astrbot_plugin_law_assistant/library/update",
            json={
                "item_id": archived["item_id"],
                "changes": {"explanation": "SECRET_UPDATE_RESPONSE"},
            },
        )
    update_payload = await update_response.get_json()

    review_id = service.library_service.repository.record_review_item(
        source_id=archived["source_id"],
        candidate_key="web-review-secret",
        material_type="real_question_candidate",
        locator="PDF第1页",
        raw_fragment="WEB_REVIEW_RAW SECRET_WEB_REVIEW_ANSWER",
        proposed_structure={
            "stem": "WEB_REVIEW_SAFE_STEM",
            "answer": "SECRET_WEB_REVIEW_ANSWER",
        },
        review_reason="复核合成题",
        now=datetime.now(timezone.utc),
    )
    async with app.test_client() as client:
        review_list_response = await client.get(
            "/api/plug/astrbot_plugin_law_assistant/reviews"
        )
        review_list_payload = await review_list_response.get_json()
        review_detail_response = await client.get(
            "/api/plug/astrbot_plugin_law_assistant/review",
            query_string={"id": review_id},
        )
        review_detail_payload = await review_detail_response.get_json()

    for payload in (detail_payload, update_payload):
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            "SECRET_ANSWER",
            "SECRET_EXPLANATION",
            "SECRET_RAW_SOURCE",
            "SECRET_UPDATE_RESPONSE",
        ):
            assert secret not in serialized
    assert detail_payload["data"]["question"]["stem"] == "SAFE_WEB_STEM"
    assert update_payload["data"]["success"] is True
    internal = await service.library_service.get_learning_item_for_session(
        archived["item_id"]
    )
    assert internal["question"]["explanation"] == "SECRET_UPDATE_RESPONSE"
    for payload in (review_list_payload, review_detail_payload):
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "SECRET_WEB_REVIEW_ANSWER" not in serialized
        assert "WEB_REVIEW_RAW" not in serialized
    service.storage.close()


@pytest.mark.asyncio
async def test_web_review_list_and_detail_project_structured_options_safely(tmp_path):
    service = _service(tmp_path)
    source_id = service.library_service.record_source(
        LibrarySource(
            source_kind="user_text",
            title="synthetic objective source",
            raw_text="synthetic objective source",
            source_url="",
            content_hash="synthetic-objective-review-source",
            created_at=datetime.now(timezone.utc),
            created_by="42",
            session_origin="private:42",
        )
    )
    review_id = service.library_service.repository.record_review_item(
        source_id=source_id,
        candidate_key="synthetic-api-dict-options",
        material_type="real_question_candidate",
        locator="PDF第1页",
        raw_fragment="SECRET_API_REVIEW_RAW",
        proposed_structure={
            "question_type": "single_choice",
            "stem": "API_SAFE_REVIEW_STEM",
            "options": [
                {"key": "A", "text": "API_SAFE_ALPHA", "locator": "PDF第1页"},
                {"key": "B", "text": "API_SAFE_BETA", "locator": "PDF第1页"},
            ],
            "answer": {"keys": ["B"]},
            "explanation_blocks": [{"text": "SECRET_API_EXPLANATION"}],
        },
        review_reason="合成 API 选择题复核",
        now=datetime.now(timezone.utc),
    )
    app = _app_for(LawAssistantWebApi(service))

    async with app.test_client() as client:
        list_response = await client.get(
            "/api/plug/astrbot_plugin_law_assistant/reviews"
        )
        list_payload = await list_response.get_json()
        detail_response = await client.get(
            "/api/plug/astrbot_plugin_law_assistant/review",
            query_string={"id": review_id},
        )
        detail_payload = await detail_response.get_json()

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert list_payload["success"] is True
    assert detail_payload["success"] is True
    listed_item = list_payload["data"]["items"][0]
    detail_item = detail_payload["data"]["item"]
    expected_preview = "API_SAFE_REVIEW_STEM\nA. API_SAFE_ALPHA\nB. API_SAFE_BETA"
    assert listed_item["safe_preview"] == expected_preview
    assert detail_item["safe_preview"] == expected_preview
    assert detail_item["safe_structure"]["options"] == [
        {"key": "A", "text": "API_SAFE_ALPHA", "locator": "PDF第1页"},
        {"key": "B", "text": "API_SAFE_BETA", "locator": "PDF第1页"},
    ]
    serialized = json.dumps(
        {"list": list_payload, "detail": detail_payload}, ensure_ascii=False
    )
    for secret in (
        "SECRET_API_REVIEW_RAW",
        "SECRET_API_EXPLANATION",
        '"keys": ["B"]',
    ):
        assert secret not in serialized
    assert "answer" not in detail_item["safe_structure"]
    assert "explanation_blocks" not in detail_item["safe_structure"]
    service.storage.close()


@pytest.mark.asyncio
async def test_dashboard_uses_verified_inventory_and_safe_daily_history(tmp_path):
    service = _service(tmp_path)
    service.storage.import_real_questions(
        [
            {
                "source_name": "synthetic verified fixture",
                "exam_name": "synthetic exam",
                "exam_year": "2024",
                "question_number": f"Q{index}",
                "source_locator": f"synthetic page {index}",
                "subject": "criminal_law",
                "question_type": "single_choice",
                "stem": f"synthetic stem {index}",
                "options": ["A", "B"],
                "answer": "A",
                "answer_source": "official",
                "verification_status": "verified",
            }
            for index in range(1, 3)
        ]
    )
    await service.library_service.archive_learning_material(
        raw_text="pending candidate fixture",
        material_type="real_question_candidate",
        title="待核验候选题",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "single_choice",
                "stem": "candidate stem",
                "answer": "A",
            }
        ),
        created_by="42",
        session_origin="private:42",
    )
    target = await service.bind_target("aiocqhttp:group:history", "历史测试群")
    service.storage.claim_daily_content(
        content_date="2026-09-27",
        target_id=target["id"],
        content_type="daily_question",
        body={"question": "visible question", "answer": "SECRET_DAILY_ANSWER"},
        source_kind="library_mock",
        source_item_key="fixture-1",
        resolved_subject="criminal_law",
        resolved_question_type="single_choice",
        resolved_origin="mock",
    )

    overview = await service.dashboard_overview()
    history = await service.dashboard_history("daily")
    for rows in (overview["recent"]["daily"], history):
        serialized = json.dumps(rows, ensure_ascii=False)
        assert "SECRET_DAILY_ANSWER" not in serialized
        assert all("body" not in row and "body_json" not in row for row in rows)
    assert overview["learning"]["verified_real"] == 2
    assert overview["learning"]["real_question_candidate"] == 1
    assert "SECRET_DAILY_ANSWER" in json.dumps(
        service.storage.list_daily_contents(), ensure_ascii=False
    )
    service.storage.close()
