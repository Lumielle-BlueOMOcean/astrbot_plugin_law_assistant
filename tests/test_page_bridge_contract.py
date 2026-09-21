from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

PAGE_ROOT = Path(__file__).parents[1] / "pages" / "law-assistant"


def _astrbot_bridge_unwrap(http_response: dict) -> Any:
    """Model AstrBot 4.25's response.data?.data ?? response.data behavior."""
    payload = http_response.get("data")
    return payload if payload is not None else http_response


def _page_api_result(bridge_result: Any) -> Any:
    """Model the page helper after the Bridge has already unwrapped the response."""
    if bridge_result is None or (
        isinstance(bridge_result, dict) and bridge_result.get("success") is False
    ):
        message = (bridge_result or {}).get("message", "请求失败")
        raise RuntimeError(message)
    return bridge_result


def test_page_helpers_consume_unwrapped_bridge_payloads():
    fixtures = [
        (
            {"success": True, "data": {"plugin": {"version": "0.3.0"}}},
            {"plugin": {"version": "0.3.0"}},
        ),
        ({"success": True, "data": {"items": [{"id": 1}]}}, {"items": [{"id": 1}]}),
        ({"success": True, "data": [{"id": 7}]}, [{"id": 7}]),
        (
            {"success": True, "data": {"global": {}, "targets": []}},
            {"global": {}, "targets": []},
        ),
        (
            {
                "success": True,
                "data": {"staged_path": "imports/a.txt", "original_filename": "a.txt"},
            },
            {"staged_path": "imports/a.txt", "original_filename": "a.txt"},
        ),
        (
            {
                "success": True,
                "data": {"token": "token-1", "preview": {"total_candidates": 1}},
            },
            {"token": "token-1", "preview": {"total_candidates": 1}},
        ),
    ]

    for http_response, expected in fixtures:
        bridge_result = _astrbot_bridge_unwrap(http_response)
        assert _page_api_result(bridge_result) == expected

    with pytest.raises(RuntimeError, match="参数错误"):
        _page_api_result({"success": False, "message": "参数错误"})


def test_app_uses_bridge_payload_without_second_data_unwrap():
    javascript = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
    api_get_start = javascript.index("async function apiGet")
    api_post_start = javascript.index("async function apiPost")
    render_imports_start = javascript.index("async function renderImports")
    api_helpers = javascript[api_get_start:render_imports_start]
    upload_block = javascript[
        render_imports_start : javascript.index("function renderImportPreview")
    ]

    assert "return result;" in api_helpers
    assert "return result.data;" not in api_helpers
    assert "result == null || result.success === false" in api_helpers
    assert "staged.staged_path" in upload_block
    assert "staged.original_filename" in upload_block
    assert "staged.data.staged_path" not in upload_block
    assert "staged.data.original_filename" not in upload_block
    assert api_get_start < api_post_start
