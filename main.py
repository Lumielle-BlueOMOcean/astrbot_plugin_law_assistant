"""AstrBot entrypoint for 微光·法务助手 / Lumielle Law Assistant."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

if __package__:
    from .config import PluginConfig
    from .extraction import EventExtractor
    from .http_client import AsyncHttpClient
    from .learning_service import LearningService
    from .llm_service import LLMService
    from .publisher import AstrBotPublisher
    from .scheduler import LawAssistantScheduler
    from .service import LawAssistantService, ScanResult
    from .sources.cases import CaseDetailExtractor, CaseSourceAdapter
    from .sources.china_jm import ChinaJMSourceAdapter
    from .sources.generic import GenericEventSourceAdapter
    from .sources.law_updates import LawUpdateExtractor
    from .storage import SQLiteStorage
else:
    from config import PluginConfig
    from extraction import EventExtractor
    from http_client import AsyncHttpClient
    from learning_service import LearningService
    from llm_service import LLMService
    from publisher import AstrBotPublisher
    from scheduler import LawAssistantScheduler
    from service import LawAssistantService, ScanResult
    from sources.cases import CaseDetailExtractor, CaseSourceAdapter
    from sources.china_jm import ChinaJMSourceAdapter
    from sources.generic import GenericEventSourceAdapter
    from sources.law_updates import LawUpdateExtractor
    from storage import SQLiteStorage

PLUGIN_NAME = "astrbot_plugin_law_assistant"


class LawAssistant(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        self.plugin_config = PluginConfig.from_mapping(config)
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.storage = SQLiteStorage(data_dir / "law_assistant.sqlite3")
        self.http = AsyncHttpClient(
            timeout_seconds=self.plugin_config.http_timeout_seconds
        )
        llm_service = LLMService(
            context,
            self.plugin_config.llm_provider_id,
            logger=logger,
        )
        event_sources = [
            (
                ChinaJMSourceAdapter(self.http),
                EventExtractor(
                    timezone_name=self.plugin_config.timezone,
                    llm_service=llm_service,
                ),
            )
        ]
        for index, url in enumerate(self.plugin_config.extra_event_source_urls, 1):
            key = f"extra:{index}"
            event_sources.append(
                (
                    GenericEventSourceAdapter(key, url, self.http),
                    EventExtractor(
                        timezone_name=self.plugin_config.timezone,
                        llm_service=llm_service,
                    ),
                )
            )

        case_sources = []
        if self.plugin_config.case_source_court_enabled:
            case_sources.append(
                (
                    CaseSourceAdapter(
                        "court_cases",
                        "最高人民法院",
                        "https://www.court.gov.cn/zixun/gengduo/104.html",
                        self.http,
                    ),
                    CaseDetailExtractor(
                        "最高人民法院", timezone_name=self.plugin_config.timezone
                    ),
                )
            )
        if self.plugin_config.case_source_spp_enabled:
            case_sources.append(
                (
                    CaseSourceAdapter(
                        "spp_cases",
                        "最高人民检察院",
                        "https://www.spp.gov.cn/spp/zgjdxal/",
                        self.http,
                    ),
                    CaseDetailExtractor(
                        "最高人民检察院", timezone_name=self.plugin_config.timezone
                    ),
                )
            )

        law_sources = []
        if self.plugin_config.law_update_enabled:
            law_sources.append(
                (
                    GenericEventSourceAdapter(
                        "law_updates",
                        "https://flk.npc.gov.cn/search",
                        self.http,
                    ),
                    LawUpdateExtractor(timezone_name=self.plugin_config.timezone),
                )
            )

        self.publisher = AstrBotPublisher(context, logger=logger)
        self.learning_service = LearningService(
            self.storage,
            llm_service,
            timezone_name=self.plugin_config.timezone,
            logger=logger,
        )
        self.service = LawAssistantService(
            self.storage,
            sources=event_sources,
            case_sources=case_sources,
            law_sources=law_sources,
            publisher=self.publisher,
            learning_service=self.learning_service,
            config=self.plugin_config,
            logger=logger,
        )
        self.scheduler = LawAssistantScheduler(
            self.service,
            enabled=(
                self.plugin_config.auto_scan_enabled
                or self.plugin_config.daily_case_enabled
                or self.plugin_config.daily_question_enabled
                or self.plugin_config.law_update_enabled
            ),
            interval_minutes=self.plugin_config.scan_interval_minutes,
            logger=logger,
        )

    async def initialize(self) -> None:
        await self.scheduler.start()

    async def terminate(self) -> None:
        await self.scheduler.stop()
        await self.http.close()
        self.storage.close()

    def _authorized(
        self, event: AstrMessageEvent, *, allow_group: bool = False
    ) -> bool:
        if not event.is_private_chat() and not allow_group:
            return False
        if event.is_admin():
            return True
        return str(event.get_sender_id()).strip() in self.plugin_config.operator_ids

    @staticmethod
    def _denial(event: AstrMessageEvent, *, allow_group: bool = False) -> str:
        if not event.is_private_chat() and not allow_group:
            return "法务助手管理命令请私聊机器人执行。"
        return "你没有法务助手 operator 权限。"

    @filter.command("law")
    async def law(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """法务助手统一控制命令；管理扫描、发布和确认只接受私聊。"""
        parts = str(event.get_message_str() or "").strip().split()
        subcommand = parts[1].lower() if len(parts) > 1 else "help"
        allow_group = subcommand in {"bind", "unbind"}
        if not self._authorized(event, allow_group=allow_group):
            yield event.plain_result(self._denial(event, allow_group=allow_group))
            return

        if subcommand == "status":
            text = self._format_status(await self.service.status())
        elif subcommand == "scan":
            text = self._format_scan(
                await _scan_service(self.service, "command", _session_origin(event))
            )
        elif subcommand == "events":
            text = self._format_events(await self.service.list_events(limit=20))
        elif subcommand == "event" and len(parts) > 2:
            text = _json_text(await self.service.get_event(_safe_int(parts[2])))
        elif subcommand == "deadlines":
            text = _json_text(await self.service.list_deadlines())
        elif subcommand == "sources":
            text = _json_text(await self.service.list_sources())
        elif subcommand == "targets":
            text = _json_text(await self.service.list_targets())
        elif subcommand == "bind":
            target = parts[2] if len(parts) > 2 else _session_origin(event)
            text = _json_text(
                await self.service.bind_target(target, " ".join(parts[3:]))
            )
        elif subcommand == "unbind":
            target = parts[2] if len(parts) > 2 else _session_origin(event)
            text = (
                "已解除发布目标。"
                if await self.service.unbind_target(target)
                else "未找到该发布目标。"
            )
        elif subcommand == "publish" and len(parts) > 2:
            text = _json_text(
                await self.service.prepare_publish_event(_safe_int(parts[2]))
            )
        elif subcommand == "confirm" and len(parts) > 2:
            text = _json_text(await self.service.confirm_publish(parts[2]))
        elif subcommand == "case":
            text = _json_text(
                await self.service.get_daily_case(session_origin=_session_origin(event))
            )
        elif subcommand == "question":
            text = _json_text(
                await self.service.generate_question(
                    " ".join(parts[2:]), session_origin=_session_origin(event)
                )
            )
        elif subcommand == "laws":
            text = _json_text(await self.service.list_law_updates())
        else:
            text = self._help_text()
        yield event.plain_result(text)

    @filter.llm_tool(name="law_status")
    async def law_status(self, event: AstrMessageEvent) -> str:
        """查看法务助手运行状态。"""
        if not self._authorized(event):
            return self._denial(event)
        return json.dumps(
            await self.service.status(), ensure_ascii=False, sort_keys=True
        )

    @filter.llm_tool(name="law_scan_events")
    async def law_scan_events(self, event: AstrMessageEvent) -> str:
        """扫描已注册的法律活动信息源并返回结构化结果。"""
        if not self._authorized(event):
            return self._denial(event)
        result = await _scan_service(self.service, "llm_tool", _session_origin(event))
        return json.dumps(self._scan_dict(result), ensure_ascii=False, sort_keys=True)

    @filter.llm_tool(name="law_list_events")
    async def law_list_events(self, event: AstrMessageEvent, limit: int = 20) -> str:
        """列出已发现的法律活动。

        Args:
            limit(number): 返回的最大活动数量，默认 20。
        """
        if not self._authorized(event):
            return self._denial(event)
        events = await self.service.list_events(limit=limit)
        return json.dumps([_event_dict(item) for item in events], ensure_ascii=False)

    @filter.llm_tool(name="law_get_event")
    async def law_get_event(self, event: AstrMessageEvent, event_id: int) -> str:
        """按内部 ID 查看单个法律活动及其时间线。

        Args:
            event_id(number): 活动内部 ID。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.get_event(event_id))

    @filter.llm_tool(name="law_list_deadlines")
    async def law_list_deadlines(self, event: AstrMessageEvent, limit: int = 20) -> str:
        """列出尚未到期且有原文证据的报名或投稿截止时间。

        Args:
            limit(number): 返回的最大截止事项数量，默认 20。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.list_deadlines(limit=limit))

    @filter.llm_tool(name="law_get_daily_case")
    async def law_get_daily_case(self, event: AstrMessageEvent) -> str:
        """基于已保存官方案例生成今日案例学习内容。"""
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.get_daily_case(session_origin=_session_origin(event))
        )

    @filter.llm_tool(name="law_generate_question")
    async def law_generate_question(
        self, event: AstrMessageEvent, subject: str = ""
    ) -> str:
        """生成一道法律学习题；题目会明确标注为练习，不构成法律意见。

        Args:
            subject(string): 可选法律学习主题。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.generate_question(
                subject, session_origin=_session_origin(event)
            )
        )

    @filter.llm_tool(name="law_list_law_updates")
    async def law_list_law_updates(
        self, event: AstrMessageEvent, limit: int = 20
    ) -> str:
        """列出已保存的法律法规或司法解释更新记录。

        Args:
            limit(number): 返回的最大更新数量，默认 20。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.list_law_updates(limit=limit))

    @filter.llm_tool(name="law_prepare_publish_event")
    async def law_prepare_publish_event(
        self, event: AstrMessageEvent, event_id: int
    ) -> str:
        """准备一条活动发布预览，必须随后明确确认才会发送。

        Args:
            event_id(number): 要预览的活动内部 ID。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.prepare_publish_event(event_id))

    @filter.llm_tool(name="law_confirm_publish")
    async def law_confirm_publish(self, event: AstrMessageEvent, token: str) -> str:
        """使用预览返回的短期 token 明确确认发布。

        Args:
            token(string): prepare 返回的确认 token。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.confirm_publish(token))

    @staticmethod
    def _scan_dict(result: ScanResult) -> dict[str, Any]:
        return {
            "trigger": result.trigger,
            "source_count": result.source_count,
            "discovered_count": result.discovered_count,
            "upserted_count": result.upserted_count,
            "failure_count": len(result.failures),
            "failures": [
                {"source_key": failure.source_key, "error": failure.error}
                for failure in result.failures
            ],
            "skipped": result.skipped,
        }

    @classmethod
    def _format_status(cls, status: dict[str, Any]) -> str:
        return (
            "法务助手状态："
            f"sources={status['source_count']}，events={status['event_count']}，"
            f"schema={status['schema_version']}"
        )

    @classmethod
    def _format_scan(cls, result: ScanResult) -> str:
        return (
            "扫描完成："
            f"sources={result.source_count}，discovered={result.discovered_count}，"
            f"upserted={result.upserted_count}，failures={len(result.failures)}"
        )

    @staticmethod
    def _format_events(events: list[Any]) -> str:
        if not events:
            return "当前尚未发现法律活动。"
        return "\n".join(
            f"{item.id}. {item.title}（{item.status}）\n{item.source_url}"
            for item in events
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "用法：/law status、/law scan、/law events、/law deadlines、"
            "/law case、/law question、/law bind、/law publish <id>、"
            "/law confirm <token>、/law help"
        )


def _session_origin(event: Any) -> str:
    return str(getattr(event, "unified_msg_origin", "") or "")


async def _scan_service(service: Any, trigger: str, session_origin: str) -> Any:
    try:
        return await service.scan_events(
            trigger=trigger, session_origin=session_origin or None
        )
    except TypeError as exc:
        if "session_origin" not in str(exc):
            raise
        return await service.scan_events(trigger=trigger)


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _event_dict(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "source_key": event.source_key,
        "source_item_key": event.source_item_key,
        "title": event.title,
        "source_url": event.source_url,
        "organizer": event.organizer,
        "event_type": event.event_type,
        "eligibility": event.eligibility,
        "status": event.status,
        "revision": event.revision,
        "dates": [
            {
                "kind": date.kind,
                "datetime": date.datetime.isoformat() if date.datetime else None,
                "timezone": date.timezone,
                "label": date.label,
                "evidence_text": date.evidence_text,
                "confirmed": date.confirmed,
            }
            for date in event.dates
        ],
    }


def _json_text(value: Any) -> str:
    if value is None:
        return "null"
    if hasattr(value, "source_key"):
        value = _event_dict(value)
    elif isinstance(value, list):
        value = [
            _event_dict(item) if hasattr(item, "source_key") else item for item in value
        ]
    return json.dumps(value, ensure_ascii=False, default=_json_default, sort_keys=True)


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return value.__dict__
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


__all__ = ["PLUGIN_NAME", "LawAssistant"]
