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
    from .publisher import NoopPublisher
    from .scheduler import LawAssistantScheduler
    from .service import LawAssistantService, ScanResult
    from .storage import SQLiteStorage
else:
    from config import PluginConfig
    from publisher import NoopPublisher
    from scheduler import LawAssistantScheduler
    from service import LawAssistantService, ScanResult
    from storage import SQLiteStorage

PLUGIN_NAME = "astrbot_plugin_law_assistant"


class LawAssistant(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.plugin_config = PluginConfig.from_mapping(config)
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.storage = SQLiteStorage(data_dir / "law_assistant.sqlite3")
        self.service = LawAssistantService(self.storage, logger=logger)
        self.publisher = NoopPublisher(logger=logger)
        self.scheduler = LawAssistantScheduler(
            self.service,
            enabled=self.plugin_config.auto_scan_enabled,
            interval_minutes=self.plugin_config.scan_interval_minutes,
            logger=logger,
        )

    async def initialize(self) -> None:
        await self.scheduler.start()

    async def terminate(self) -> None:
        await self.scheduler.stop()
        self.storage.close()

    def _authorized(self, event: AstrMessageEvent) -> bool:
        if not event.is_private_chat():
            return False
        if event.is_admin():
            return True
        return str(event.get_sender_id()).strip() in self.plugin_config.operator_ids

    @staticmethod
    def _denial(event: AstrMessageEvent) -> str:
        if not event.is_private_chat():
            return "法务助手管理命令请私聊机器人执行。"
        return "你没有法务助手 operator 权限。"

    @filter.command("law")
    async def law(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """法务助手基础控制命令。"""
        if not self._authorized(event):
            yield event.plain_result(self._denial(event))
            return

        parts = str(event.get_message_str() or "").strip().split()
        subcommand = parts[1].lower() if len(parts) > 1 else "help"
        if subcommand == "status":
            text = self._format_status(await self.service.status())
        elif subcommand == "scan":
            text = self._format_scan(await self.service.scan_events(trigger="command"))
        elif subcommand == "events":
            text = self._format_events(await self.service.list_events(limit=20))
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
        result = await self.service.scan_events(trigger="llm_tool")
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
        return json.dumps(
            [
                {
                    "id": item.id,
                    "title": item.title,
                    "source_url": item.source_url,
                    "status": item.status,
                    "event_type": item.event_type,
                }
                for item in events
            ],
            ensure_ascii=False,
        )

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
        return "用法：/law status、/law scan、/law events、/law help"
