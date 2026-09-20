from __future__ import annotations

import json
import sys

import pytest

from tests.fakes import FakeEvent
from tests.test_plugin import install_fake_astrbot


@pytest.fixture
def plugin_module(monkeypatch, tmp_path):
    install_fake_astrbot(monkeypatch, tmp_path / "plugin-data")
    sys.modules.pop("main", None)
    import main

    return main


@pytest.mark.asyncio
async def test_library_tools_require_operator_private_chat(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})

    denied = await plugin.law_search_learning_library(
        FakeEvent(private=True, sender_id="7"), query="案例"
    )
    group_denied = await plugin.law_search_learning_library(
        FakeEvent(private=False, sender_id="42"), query="案例"
    )

    assert "没有" in denied
    assert "私聊" in group_denied
    await plugin.terminate()


@pytest.mark.asyncio
async def test_archive_search_get_survives_plugin_restart(plugin_module):
    config = {"operator_ids": ["42"]}
    first = plugin_module.LawAssistant(None, config)
    event = FakeEvent(private=True, sender_id="42")
    archived = json.loads(
        await first.law_archive_learning_material(
            event,
            raw_text="原始案例：短视频平台传播摄影作品。",
            material_type="case",
            title="短视频著作权案例",
            subjects="知识产权",
            structured_json=json.dumps(
                {
                    "case_summary": "围绕短视频传播和著作权许可的学习案例",
                    "issues": ["是否获得授权"],
                    "practice_notes": ["保存传播链路证据"],
                },
                ensure_ascii=False,
            ),
        )
    )
    assert archived["success"] is True
    assert archived["identity"] == "user_case"
    item_id = archived["item_id"]
    await first.terminate()

    second = plugin_module.LawAssistant(None, config)
    search = json.loads(
        await second.law_search_learning_library(event, query="短视频著作权")
    )
    assert search["success"] is True
    assert search["count"] == 1
    assert search["items"][0]["id"] == item_id

    detail = json.loads(await second.law_get_learning_item(event, item_id))
    assert detail["success"] is True
    assert detail["source"]["raw_text"] == "原始案例：短视频平台传播摄影作品。"
    assert detail["item"]["subjects"] == ["intellectual_property"]
    assert detail["case"]["case_summary"] == "围绕短视频传播和著作权许可的学习案例"
    await second.terminate()


@pytest.mark.asyncio
async def test_library_tool_update_is_limited_and_returns_stable_json(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    event = FakeEvent(private=True, sender_id="42")
    archived = json.loads(
        await plugin.law_archive_learning_material(
            event,
            raw_text="一段学习资料",
            material_type="note",
            structured_json=json.dumps({"body": "原始笔记"}),
        )
    )

    updated = json.loads(
        await plugin.law_update_learning_item(
            event,
            archived["item_id"],
            title="人工修订标题",
            note="人工备注",
        )
    )
    assert updated["success"] is True
    assert updated["item"]["title"] == "人工修订标题"
    assert updated["item"]["metadata"]["note"] == "人工备注"

    invalid = await plugin.service.update_learning_item(
        archived["item_id"], {"identity": "verified_real_question"}
    )
    assert invalid["error"] == "invalid_update_field"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_library_tool_returns_structured_validation_error(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    response = json.loads(
        await plugin.law_archive_learning_material(
            FakeEvent(private=True, sender_id="42"),
            raw_text="资料",
            material_type="official_case",
        )
    )
    assert response == {
        "success": False,
        "error": "invalid_material_type",
        "message": "不支持的资料类型：official_case",
    }
    assert "Traceback" not in json.dumps(response)
    await plugin.terminate()


def test_library_tools_are_registered(plugin_module):
    for name in (
        "law_archive_learning_material",
        "law_search_learning_library",
        "law_get_learning_item",
        "law_update_learning_item",
    ):
        assert getattr(plugin_module.LawAssistant, name)._fake_llm_tool == name
