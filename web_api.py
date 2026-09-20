"""AstrBot Dashboard Web API facade for the embedded Law Assistant page."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from quart import jsonify, request

PLUGIN_NAME = "astrbot_plugin_law_assistant"


def _route(path: str) -> str:
    """Return a route relative to AstrBot's ``/api/plug`` dispatcher."""
    return f"/{PLUGIN_NAME}/{path.lstrip('/')}"


class LawAssistantWebApi:
    """Translate dashboard requests into LawAssistantService calls.

    The controller intentionally contains no SQL and no domain selection rules;
    all writes and business validation remain in the service facade.
    """

    def __init__(self, service: Any, *, logger: Any | None = None) -> None:
        self.service = service
        self.logger = logger or logging.getLogger(__name__)

    def register(self, context: Any) -> None:
        routes: tuple[
            tuple[str, Callable[..., Awaitable[Any]], list[str], str], ...
        ] = (
            ("overview", self.overview, ["GET"], "Law Assistant dashboard overview"),
            ("radar/events", self.radar_events, ["GET"], "List radar events"),
            ("radar/event", self.radar_event, ["GET"], "Get one radar event"),
            ("radar/scan", self.radar_scan, ["POST"], "Scan registered sources"),
            ("library/search", self.library_search, ["GET"], "Search learning library"),
            ("library/item", self.library_item, ["GET"], "Get learning item"),
            ("library/update", self.library_update, ["POST"], "Update learning item"),
            ("files/stage", self.stage_file, ["POST"], "Stage learning file"),
            (
                "imports/prepare",
                self.prepare_import,
                ["POST"],
                "Prepare learning import",
            ),
            (
                "imports/confirm",
                self.confirm_import,
                ["POST"],
                "Confirm learning import",
            ),
            ("reviews", self.reviews, ["GET"], "List review items"),
            ("review", self.review, ["GET"], "Get review item"),
            ("review/status", self.review_status, ["POST"], "Update review status"),
            ("plans", self.plans, ["GET"], "List daily plans"),
            ("plans/prepare", self.prepare_plan, ["POST"], "Prepare daily plan"),
            ("plans/confirm", self.confirm_plan, ["POST"], "Confirm daily plan"),
            (
                "plans/reset-prepare",
                self.prepare_plan_reset,
                ["POST"],
                "Prepare plan reset",
            ),
            (
                "plans/reset-confirm",
                self.confirm_plan_reset,
                ["POST"],
                "Confirm plan reset",
            ),
            ("targets", self.targets, ["GET"], "List publish targets"),
            ("target/rename", self.rename_target, ["POST"], "Rename publish target"),
            (
                "target/unbind-prepare",
                self.prepare_unbind,
                ["POST"],
                "Prepare target removal",
            ),
            (
                "target/unbind-confirm",
                self.confirm_unbind,
                ["POST"],
                "Confirm target removal",
            ),
            ("history/daily", self.history_daily, ["GET"], "List daily history"),
            (
                "history/publications",
                self.history_publications,
                ["GET"],
                "List publications",
            ),
            ("history/reminders", self.history_reminders, ["GET"], "List reminders"),
            ("history/sources", self.history_sources, ["GET"], "List source runs"),
        )
        for route, handler, methods, description in routes:
            context.register_web_api(
                _route(route),
                self._safe(handler),
                methods,
                description,
            )

    def _safe(
        self, handler: Callable[..., Awaitable[Any]]
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(handler)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return await handler(*args, **kwargs)
            except Exception:
                self.logger.exception("Law Assistant dashboard request failed")
                return _error("internal_error", "操作失败，请查看 AstrBot 日志", 500)

        return wrapped

    async def overview(self) -> Any:
        return _ok(await self.service.dashboard_overview())

    async def radar_events(self) -> Any:
        return _ok(
            await self.service.dashboard_radar_events(
                limit=_limit(request.args.get("limit")),
                radar_status=request.args.get("status", "current"),
                event_type=request.args.get("event_type") or None,
                keyword=request.args.get("keyword") or None,
            )
        )

    async def radar_event(self) -> Any:
        event_id = _int_arg(request.args.get("id"), "id")
        event = await self.service.get_event(event_id)
        if event is None:
            return _error("not_found", "未找到该活动", 404)
        return _ok(_event_json(event))

    async def radar_scan(self) -> Any:
        result = await self.service.scan_events(trigger="webui")
        return _ok(
            {
                "trigger": result.trigger,
                "source_count": result.source_count,
                "discovered_count": result.discovered_count,
                "upserted_count": result.upserted_count,
                "disabled_sources": list(getattr(result, "disabled_sources", ())),
                "skipped": result.skipped,
                "failures": [
                    {"source_key": item.source_key, "error": item.error}
                    for item in result.failures
                ],
                "duration_seconds": result.duration_seconds,
            }
        )

    async def library_search(self) -> Any:
        return _service_payload(
            await self.service.search_learning_library(
                query=_bounded_text(request.args.get("query", ""), 200),
                material_type=request.args.get("type", ""),
                subject=request.args.get("subject", ""),
                limit=_limit(request.args.get("limit")),
            )
        )

    async def library_item(self) -> Any:
        item_id = _int_arg(request.args.get("id"), "id")
        return _service_payload(await self.service.get_learning_item(item_id))

    async def library_update(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.update_learning_item(
                _int_arg(body.get("item_id"), "item_id"),
                body.get("changes", {}),
            )
        )

    async def stage_file(self) -> Any:
        files = await request.files
        uploaded = files.get("file")
        if uploaded is None:
            return _error("missing_file", "请上传 txt、docx 或 pdf 文件")
        data = uploaded.read()
        return _service_payload(
            await self.service.stage_learning_upload(uploaded.filename or "", data)
        )

    async def prepare_import(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.prepare_learning_import(
                _bounded_text(body.get("staged_path", ""), 300),
                content_kind=_bounded_text(body.get("content_kind", "auto"), 40),
                created_by="webui",
                session_origin="webui",
                owner_id="webui",
                original_filename=_bounded_text(body.get("original_filename", ""), 200),
            )
        )

    async def confirm_import(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.confirm_learning_import(
                body.get("token", ""), owner_id="webui"
            )
        )

    async def reviews(self) -> Any:
        return _service_payload(
            await self.service.list_learning_review_items(
                source_id=(
                    _int_arg(request.args["source_id"], "source_id")
                    if request.args.get("source_id")
                    else None
                ),
                status=request.args.get("status", "pending"),
                limit=_limit(request.args.get("limit")),
            )
        )

    async def review(self) -> Any:
        review_id = _int_arg(request.args.get("id"), "id")
        return _service_payload(await self.service.get_learning_review_item(review_id))

    async def review_status(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.update_learning_review_status(
                _int_arg(body.get("review_id"), "review_id"),
                _bounded_text(body.get("status", ""), 20),
            )
        )

    async def plans(self) -> Any:
        target = request.args.get("target", "")
        selectors = [
            item.strip()
            for item in target.replace("，", ",").split(",")
            if item.strip()
        ]
        try:
            result = await self.service.list_daily_plans(
                selectors or None, days=_limit(request.args.get("days"), maximum=31)
            )
        except (TypeError, ValueError) as exc:
            return _error("invalid_parameter", str(exc))
        return _ok(result)

    async def prepare_plan(self) -> Any:
        body = await _json_body()
        try:
            result = await self.service.prepare_daily_plan_update(
                target_selectors=_selectors(body.get("target")),
                global_scope=str(body.get("scope", "target")) == "global",
                content_type=body.get("content_type", "both"),
                changes=body.get("changes", {}),
                actor_id="webui",
            )
        except (TypeError, ValueError) as exc:
            return _error("invalid_parameter", str(exc))
        if not result.get("ready"):
            return _error("invalid_plan", result.get("reason", "计划无效"))
        return _ok(result)

    async def confirm_plan(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.confirm_daily_plan_update(
                body.get("token", ""), actor_id="webui"
            )
        )

    async def prepare_plan_reset(self) -> Any:
        body = await _json_body()
        try:
            result = await self.service.prepare_daily_plan_override_removal(
                target_selectors=_selectors(body.get("target")),
                content_type=body.get("content_type", "both"),
                actor_id="webui",
            )
        except (TypeError, ValueError) as exc:
            return _error("invalid_parameter", str(exc))
        return _ready_payload(result)

    async def confirm_plan_reset(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.confirm_daily_plan_override_removal(
                body.get("token", ""), actor_id="webui"
            )
        )

    async def targets(self) -> Any:
        return _ok(await self.service.list_targets())

    async def rename_target(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.rename_target(
                _bounded_text(body.get("target", ""), 200),
                _bounded_text(body.get("label", ""), 100),
            )
        )

    async def prepare_unbind(self) -> Any:
        body = await _json_body()
        try:
            result = await self.service.prepare_unbind_target(
                _bounded_text(body.get("target", ""), 200), actor_id="webui"
            )
        except ValueError as exc:
            return _error("invalid_parameter", str(exc))
        return _ready_payload(result)

    async def confirm_unbind(self) -> Any:
        body = await _json_body()
        return _service_payload(
            await self.service.confirm_unbind_target(
                body.get("token", ""), actor_id="webui"
            )
        )

    async def history_daily(self) -> Any:
        return await _history("daily", self.service, request.args.get("limit"))

    async def history_publications(self) -> Any:
        return await _history("publications", self.service, request.args.get("limit"))

    async def history_reminders(self) -> Any:
        return await _history("reminders", self.service, request.args.get("limit"))

    async def history_sources(self) -> Any:
        return await _history("sources", self.service, request.args.get("limit"))


async def _history(kind: str, service: Any, raw_limit: str | None) -> Any:
    try:
        return _ok(await service.dashboard_history(kind, limit=_limit(raw_limit)))
    except (TypeError, ValueError) as exc:
        return _error("invalid_parameter", str(exc))


async def _json_body() -> dict[str, Any]:
    body = await request.get_json(silent=True)
    if not isinstance(body, dict):
        raise TypeError("请求体必须是 JSON 对象")
    return body


def _selectors(value: Any) -> list[str] | None:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return [
            item.strip() for item in value.replace("，", ",").split(",") if item.strip()
        ]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("target 必须是字符串或列表")


def _bounded_text(value: Any, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError(f"文本长度不能超过 {maximum} 个字符")
    return text


def _limit(value: Any, *, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value or 50), maximum))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit 必须是整数") from exc


def _int_arg(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _service_payload(result: Any) -> Any:
    if isinstance(result, dict) and result.get("success") is False:
        return _error(
            str(result.get("error") or "business_error"),
            str(result.get("message") or result.get("reason") or "操作失败"),
        )
    return _ok(result)


def _ready_payload(result: dict[str, Any]) -> Any:
    if not result.get("ready"):
        return _error("business_error", str(result.get("reason") or "操作未准备好"))
    return _ok(result)


def _ok(data: Any) -> Any:
    return jsonify({"success": True, "data": _jsonable(data)})


def _error(error: str, message: str, status: int = 400) -> Any:
    response = jsonify({"success": False, "error": error, "message": message})
    response.status_code = status
    return response


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _event_json(event: Any) -> dict[str, Any]:
    data = _jsonable(event)
    if isinstance(data, dict):
        metadata = data.get("metadata") or {}
        data["radar_status"] = metadata.get("radar_status")
        data.pop("metadata", None)
    return data


__all__ = ["LawAssistantWebApi"]
