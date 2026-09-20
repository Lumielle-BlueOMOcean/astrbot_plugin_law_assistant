"""AstrBot entrypoint for 微光·法务助手 / Lumielle Law Assistant."""

from __future__ import annotations

import json
import shlex
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

if __package__:
    from .config import PluginConfig
    from .document_ingestion import DocumentIngestionService
    from .extraction import EventExtractor
    from .http_client import AsyncHttpClient
    from .learning_service import LearningService
    from .library_repository import LibraryRepository
    from .library_service import LibraryService
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
    from document_ingestion import DocumentIngestionService
    from extraction import EventExtractor
    from http_client import AsyncHttpClient
    from learning_service import LearningService
    from library_repository import LibraryRepository
    from library_service import LibraryService
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
                    activity_keywords=self.plugin_config.radar_keywords,
                    action_keywords=self.plugin_config.radar_action_keywords,
                    historical_keywords=self.plugin_config.radar_historical_keywords,
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
                        activity_keywords=self.plugin_config.radar_keywords,
                        action_keywords=self.plugin_config.radar_action_keywords,
                        historical_keywords=self.plugin_config.radar_historical_keywords,
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
            library_service=None,
            daily_case_card_max_chars=self.plugin_config.daily_case_card_max_chars,
        )
        self.library_service = LibraryService(
            LibraryRepository(self.storage.connection)
        )
        self.document_ingestion = DocumentIngestionService(
            data_dir,
            self.library_service,
        )
        self.learning_service.library_service = self.library_service
        self.service = LawAssistantService(
            self.storage,
            sources=event_sources,
            case_sources=case_sources,
            law_sources=law_sources,
            publisher=self.publisher,
            learning_service=self.learning_service,
            library_service=self.library_service,
            document_ingestion=self.document_ingestion,
            config=self.plugin_config,
            logger=logger,
        )
        self.service.materialize_config_rotation_anchors()
        self.scheduler = LawAssistantScheduler(
            self.service,
            enabled=(
                self.plugin_config.auto_scan_enabled
                or self.plugin_config.daily_case_enabled
                or self.plugin_config.daily_question_enabled
                or self.plugin_config.law_update_enabled
                or any(
                    bool(item.get("enabled"))
                    for item in self.storage.list_daily_plans()
                )
            ),
            interval_minutes=self.plugin_config.scan_interval_minutes,
            logger=logger,
        )
        self.service.set_scheduler_wakeup(self._wake_scheduler)

    async def initialize(self) -> None:
        await self.scheduler.start()

    async def terminate(self) -> None:
        await self.scheduler.stop()
        await self.http.close()
        self.storage.close()

    async def _wake_scheduler(self) -> None:
        self.scheduler.enabled = True
        await self.scheduler.start()

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
        message_text = str(event.get_message_str() or "").strip()
        question_import_path = _extract_question_import_path(message_text)
        try:
            parts = shlex.split(message_text)
        except ValueError as exc:
            yield event.plain_result(f"命令格式错误：{exc}")
            return
        subcommand = parts[1].lower() if len(parts) > 1 else "help"
        allow_group = subcommand in {"bind", "bind-umo", "unbind", "rename"}
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
            radar_status, event_type, keyword = _parse_event_args(parts[2:])
            try:
                text = self._format_events(
                    await self.service.list_events(
                        limit=20,
                        radar_status=radar_status,
                        event_type=event_type,
                        keyword=keyword,
                    )
                )
            except ValueError as exc:
                text = str(exc)
        elif subcommand == "event" and len(parts) > 2:
            text = _json_text(await self.service.get_event(_safe_int(parts[2])))
        elif subcommand == "deadlines":
            text = _json_text(await self.service.list_deadlines())
        elif subcommand == "sources":
            text = _json_text(await self.service.list_sources())
        elif subcommand == "targets":
            text = _json_text(await self.service.list_targets())
        elif subcommand == "bind":
            if event.is_private_chat():
                if len(parts) < 3 or not _looks_like_umo(parts[2]):
                    text = (
                        "私聊绑定请使用 /law bind-umo <unified_msg_origin> [群别名]。"
                    )
                else:
                    text = _json_text(
                        await self.service.bind_target(parts[2], " ".join(parts[3:]))
                    )
            else:
                text = _json_text(
                    await self.service.bind_target(
                        _session_origin(event), " ".join(parts[2:])
                    )
                )
        elif subcommand == "bind-umo" and len(parts) > 2:
            text = _json_text(
                await self.service.bind_target(parts[2], " ".join(parts[3:]))
            )
        elif subcommand == "unbind":
            target = parts[2] if len(parts) > 2 else _session_origin(event)
            text = (
                "已解除发布目标。"
                if await self.service.unbind_target(target)
                else "未找到该发布目标。"
            )
        elif subcommand == "rename":
            if event.is_private_chat():
                if len(parts) < 4:
                    text = "私聊改名请使用 /law rename <目标群名或 ID> <新别名>。"
                else:
                    text = _json_text(
                        await self.service.rename_target(parts[2], " ".join(parts[3:]))
                    )
            elif len(parts) > 2:
                text = _json_text(
                    await self.service.rename_target(
                        _session_origin(event), " ".join(parts[2:])
                    )
                )
            else:
                text = "群聊改名请使用 /law rename <新别名>。"
        elif subcommand == "publish" and len(parts) > 2:
            text = _json_text(
                await self.service.prepare_publish_event(
                    _safe_int(parts[2]),
                    _split_targets(" ".join(parts[3:])),
                    actor_id=str(event.get_sender_id()),
                )
            )
        elif subcommand == "confirm" and len(parts) > 2:
            text = _json_text(
                await self.service.confirm_publish(
                    parts[2], actor_id=str(event.get_sender_id())
                )
            )
        elif subcommand == "case":
            text = _json_text(
                await self.service.get_daily_case(
                    subject=" ".join(parts[2:]) or None,
                    session_origin=_session_origin(event),
                    actor_id=str(event.get_sender_id()),
                )
            )
        elif subcommand == "question":
            origin, subject, question_type = _parse_question_args(parts[2:])
            text = _json_text(
                await self.service.generate_question(
                    subject or "",
                    origin=origin,
                    question_type=question_type,
                    session_origin=_session_origin(event),
                    actor_id=str(event.get_sender_id()),
                )
            )
        elif subcommand == "import" and len(parts) > 2:
            relative_path, content_kind = _parse_document_import_args(parts[2:])
            text = _json_text(
                await self.service.import_learning_document(
                    relative_path,
                    created_by=str(event.get_sender_id()),
                    session_origin=_session_origin(event),
                    content_kind=content_kind,
                )
            )
        elif subcommand in {"plans", "plan"}:
            selector = " ".join(parts[2:]).strip()
            try:
                text = _json_text(
                    await self.service.list_daily_plans(
                        _split_targets(selector) if selector else None
                    )
                )
            except ValueError as exc:
                text = str(exc)
        elif subcommand in {"question-import", "import-questions"} and len(parts) > 2:
            text = _json_text(
                self.service.import_real_questions_file(
                    question_import_path or " ".join(parts[2:])
                )
            )
        elif subcommand == "case-tag" and len(parts) > 2:
            text = _json_text(
                await self.service.set_case_subjects(_safe_int(parts[2]), parts[3:])
            )
        elif subcommand == "review":
            source_id = _safe_int(parts[2]) if len(parts) > 2 else None
            text = _json_text(
                await self.service.list_learning_review_items(
                    source_id=source_id if source_id and source_id > 0 else None
                )
            )
        elif subcommand == "review-get" and len(parts) > 2:
            text = _json_text(
                await self.service.get_learning_review_item(_safe_int(parts[2]))
            )
        elif subcommand == "review-status" and len(parts) > 3:
            text = _json_text(
                await self.service.update_learning_review_status(
                    _safe_int(parts[2]), parts[3]
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
    async def law_list_events(
        self,
        event: AstrMessageEvent,
        limit: int = 20,
        radar_status: str = "current",
        event_type: str = "",
        keyword: str = "",
    ) -> str:
        """列出已发现的法律活动。

        Args:
            limit(number): 返回的最大活动数量，默认 20。
            radar_status(string): current、needs_review、historical 或 all，默认 current。
            event_type(string): 可选活动类型过滤。
            keyword(string): 可选标题、主办方或资格关键词过滤。
        """
        if not self._authorized(event):
            return self._denial(event)
        try:
            try:
                events = await self.service.list_events(
                    limit=limit,
                    radar_status=radar_status or "current",
                    event_type=event_type or None,
                    keyword=keyword or None,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                events = await self.service.list_events(limit=limit)
        except ValueError as exc:
            return str(exc)
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
    async def law_get_daily_case(
        self, event: AstrMessageEvent, subject: str = ""
    ) -> str:
        """基于已保存官方案例生成今日案例学习内容。"""
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.get_daily_case(
                subject=subject or None,
                session_origin=_session_origin(event),
                actor_id=str(event.get_sender_id()),
            )
        )

    @filter.llm_tool(name="law_generate_question")
    async def law_generate_question(
        self,
        event: AstrMessageEvent,
        subject: str = "",
        origin: str = "random",
        question_type: str = "",
    ) -> str:
        """按来源、方向和题型获取一道题目；真题只来自已核验题库。

        Args:
            subject(string): 可选方向，如刑法、民商法、知识产权。
            origin(string): real、mock 或 random，默认 random。
            question_type(string): 单选、多选、判断、简答或案例分析；留空随机。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.generate_question(
                subject,
                origin=origin,
                question_type=question_type or None,
                session_origin=_session_origin(event),
                actor_id=str(event.get_sender_id()),
            )
        )

    @filter.llm_tool(name="law_question_inventory")
    async def law_question_inventory(self, event: AstrMessageEvent) -> str:
        """查询已导入且已核验真题的数量、方向和题型覆盖。"""
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.question_inventory())

    @filter.llm_tool(name="law_archive_learning_material")
    async def law_archive_learning_material(
        self,
        event: AstrMessageEvent,
        raw_text: str,
        material_type: str,
        title: str = "",
        subjects: str = "",
        source_url: str = "",
        structured_json: str = "{}",
        note: str = "",
    ) -> str:
        """仅在用户明确要求收藏、保存、归档或加入题库时保存学习资料。

        Args:
            raw_text(string): 用户交给机器人的原始资料正文，必须原样保留。
            material_type(string): case、real_question_candidate、mock_question 或 note。
            title(string): 可选标题。
            subjects(string): 可选法律方向，多个方向用逗号或顿号分隔。
            source_url(string): 可选来源 URL。
            structured_json(string): Agent 整理出的 JSON；不能授予 official/verified 身份。
            note(string): 可选人工备注。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.archive_learning_material(
                raw_text=raw_text,
                material_type=material_type,
                title=title,
                subjects=subjects,
                source_url=source_url,
                structured_json=structured_json,
                note=note,
                created_by=str(event.get_sender_id()),
                session_origin=_session_origin(event),
            )
        )

    @filter.llm_tool(name="law_import_learning_document")
    async def law_import_learning_document(
        self,
        event: AstrMessageEvent,
        relative_path: str,
        content_kind: str = "auto",
    ) -> str:
        """从插件受控 imports 目录导入 TXT、DOCX 或文本型 PDF。

        Args:
            relative_path(string): imports 目录内的相对路径，不能是绝对路径。
            content_kind(string): auto、case、mock_question 或 real_question_candidate。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.import_learning_document(
                relative_path,
                created_by=str(event.get_sender_id()),
                session_origin=_session_origin(event),
                content_kind=content_kind,
            )
        )

    @filter.llm_tool(name="law_search_learning_library")
    async def law_search_learning_library(
        self,
        event: AstrMessageEvent,
        query: str = "",
        material_type: str = "",
        subject: str = "",
        limit: int = 10,
    ) -> str:
        """搜索已收藏的学习资料，不会自动创建或修改资料。

        Args:
            query(string): 可选关键词，搜索标题、原文、摘要、题干和要点。
            material_type(string): 可选 case、question、note、real_question_candidate 或 mock_question。
            subject(string): 可选法律方向。
            limit(number): 返回数量，默认 10。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.search_learning_library(
                query=query,
                material_type=material_type,
                subject=subject,
                limit=limit,
            )
        )

    @filter.llm_tool(name="law_get_learning_item")
    async def law_get_learning_item(self, event: AstrMessageEvent, item_id: int) -> str:
        """读取一条完整学习资料，包括原始来源和结构化内容。

        Args:
            item_id(number): 搜索结果中的学习条目 ID。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.get_learning_item(item_id))

    @filter.llm_tool(name="law_update_learning_item")
    async def law_update_learning_item(
        self,
        event: AstrMessageEvent,
        item_id: int,
        title: str = "",
        subjects: str = "",
        note: str = "",
        practice_notes: str = "",
        explanation: str = "",
    ) -> str:
        """修改学习条目的标题、方向、备注或学习解析；不能升级官方/核验身份。

        Args:
            item_id(number): 要修改的学习条目 ID。
            title(string): 可选新标题。
            subjects(string): 可选新方向，多个方向用逗号或顿号分隔。
            note(string): 可选人工备注。
            practice_notes(string): 可选案例学习要点。
            explanation(string): 可选题目学习解析。
        """
        if not self._authorized(event):
            return self._denial(event)
        changes = {
            key: value
            for key, value in {
                "title": title,
                "subjects": subjects,
                "note": note,
                "practice_notes": practice_notes,
                "explanation": explanation,
            }.items()
            if str(value or "").strip()
        }
        return _json_text(await self.service.update_learning_item(item_id, changes))

    @filter.llm_tool(name="law_list_targets")
    async def law_list_targets(self, event: AstrMessageEvent) -> str:
        """列出可定向发布的已启用群目标及其人类可读名称。"""
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.list_targets())

    @filter.llm_tool(name="law_rename_target")
    async def law_rename_target(
        self, event: AstrMessageEvent, target: str, label: str
    ) -> str:
        """将明确指定的已绑定群目标改为新的可读别名。"""
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(await self.service.rename_target(target, label))

    @filter.llm_tool(name="law_get_daily_plans")
    async def law_get_daily_plans(
        self, event: AstrMessageEvent, target: str = "", days: int = 7
    ) -> str:
        """查看全局默认和群级每日案例/每日一题计划及未来安排。

        Args:
            target(string): 可选群名或目标 ID；留空查看全部已绑定群。
            days(number): 预览天数，默认 7。
        """
        if not self._authorized(event):
            return self._denial(event)
        try:
            result = await self.service.list_daily_plans(
                _split_targets(target) if target else None, days=days
            )
        except (TypeError, ValueError) as exc:
            return str(exc)
        return _json_text(result)

    @filter.llm_tool(name="law_prepare_publish_question")
    async def law_prepare_publish_question(
        self,
        event: AstrMessageEvent,
        origin: str = "random",
        subject: str = "",
        question_type: str = "",
        target: str = "",
        content_ref: str = "",
    ) -> str:
        """生成并预览一题后定向发布；必须用 law_confirm_publish 明确确认。

        Args:
            origin(string): real、mock 或 random。
            subject(string): 方向，可用中文名称。
            question_type(string): 题型；留空随机。
            target(string): 群名、目标 ID，或用逗号分隔的多个群；留空时仅有一个群才自动选择。
            content_ref(string): law_generate_question 返回的短期内容引用；提供时复用刚才的原题。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.prepare_publish_question(
                origin=origin,
                subject=subject or None,
                question_type=question_type or None,
                target_selectors=_split_targets(target) if target else None,
                session_origin=_session_origin(event),
                actor_id=str(event.get_sender_id()),
                content_ref=content_ref or None,
            )
        )

    @filter.llm_tool(name="law_prepare_publish_case")
    async def law_prepare_publish_case(
        self,
        event: AstrMessageEvent,
        subject: str = "",
        target: str = "",
        content_ref: str = "",
    ) -> str:
        """按方向选择官方案例并预览定向发布；必须明确确认。

        Args:
            subject(string): 可选案例方向。
            target(string): 群名、目标 ID，或逗号分隔的多个群。
            content_ref(string): law_get_daily_case 返回的短期内容引用；提供时复用刚才的案例。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.prepare_publish_case(
                subject=subject or None,
                target_selectors=_split_targets(target) if target else None,
                session_origin=_session_origin(event),
                actor_id=str(event.get_sender_id()),
                content_ref=content_ref or None,
            )
        )

    @filter.llm_tool(name="law_prepare_daily_plan_update")
    async def law_prepare_daily_plan_update(
        self,
        event: AstrMessageEvent,
        scope: str = "target",
        target: str = "",
        content_type: str = "both",
        enabled: str = "",
        time: str = "",
        selection_mode: str = "",
        fixed_subject: str = "",
        rotation_subjects: str = "",
        rotation_start_date: str = "",
        rotation_start_index: int = -1,
        question_origin: str = "",
        question_type: str = "",
    ) -> str:
        """预览每日案例/每日一题长期计划变更；必须随后明确确认。

        Args:
            scope(string): target 修改单个群，global 修改全局默认。
            target(string): 群名或目标 ID；scope=target 时必填，单群时可留空。
            content_type(string): daily_case、daily_question 或 both。
            enabled(string): true/false；留空保持原值。
            time(string): HH:MM；留空保持原值。
            selection_mode(string): random、fixed 或 rotation；留空保持原值。
            fixed_subject(string): fixed 模式方向。
            rotation_subjects(string): 用逗号、顿号或“和”分隔的有序方向列表；也可填“学校四方向”。
            rotation_start_date(string): YYYY-MM-DD。
            rotation_start_index(number): 起始位置；负数保持原值。
            question_origin(string): real、mock 或 random。
            question_type(string): 题型；留空保持原值。
        """
        if not self._authorized(event):
            return self._denial(event)
        changes: dict[str, Any] = {}
        if enabled.strip():
            changes["enabled"] = enabled.strip().lower() in {"1", "true", "yes", "on"}
        for key, value in {
            "time": time,
            "selection_mode": selection_mode,
            "fixed_subject": fixed_subject,
            "rotation_start_date": rotation_start_date,
            "question_origin": question_origin,
            "question_type": question_type,
        }.items():
            if value.strip():
                changes[key] = value.strip()
        if rotation_subjects.strip():
            changes["rotation_subjects"] = _split_targets(rotation_subjects)
        if rotation_start_index >= 0:
            changes["rotation_start_index"] = rotation_start_index
        try:
            result = await self.service.prepare_daily_plan_update(
                target_selectors=_split_targets(target) if target else None,
                global_scope=scope.strip().lower() == "global",
                content_type=content_type.strip() or "both",
                changes=changes,
                actor_id=str(event.get_sender_id()),
            )
        except ValueError as exc:
            return str(exc)
        return _json_text(result)

    @filter.llm_tool(name="law_confirm_daily_plan_update")
    async def law_confirm_daily_plan_update(
        self, event: AstrMessageEvent, token: str
    ) -> str:
        """确认并保存 law_prepare_daily_plan_update 返回的计划变更 token。"""
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.confirm_daily_plan_update(
                token, actor_id=str(event.get_sender_id())
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
        self, event: AstrMessageEvent, event_id: int, target: str = ""
    ) -> str:
        """准备一条活动发布预览，必须随后明确确认才会发送。

        Args:
            event_id(number): 要预览的活动内部 ID。
            target(string): 可选群名、目标 ID，或逗号分隔的多个群。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.prepare_publish_event(
                event_id,
                _split_targets(target) if target else None,
                actor_id=str(event.get_sender_id()),
            )
        )

    @filter.llm_tool(name="law_confirm_publish")
    async def law_confirm_publish(self, event: AstrMessageEvent, token: str) -> str:
        """使用预览返回的短期 token 明确确认发布。

        Args:
            token(string): prepare 返回的确认 token。
        """
        if not self._authorized(event):
            return self._denial(event)
        return _json_text(
            await self.service.confirm_publish(
                token, actor_id=str(event.get_sender_id())
            )
        )

    @staticmethod
    def _scan_dict(result: ScanResult) -> dict[str, Any]:
        return {
            "trigger": result.trigger,
            "source_count": result.source_count,
            "discovered_count": result.discovered_count,
            "upserted_count": result.upserted_count,
            "disabled_sources": list(getattr(result, "disabled_sources", ())),
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
            f"upserted={result.upserted_count}，failures={len(result.failures)}，"
            f"disabled={len(getattr(result, 'disabled_sources', ()))}"
        )

    @staticmethod
    def _format_events(events: list[Any]) -> str:
        if not events:
            return "当前尚未发现法律活动。"
        return "\n".join(
            f"{item.id}. {item.title}（{item.metadata.get('radar_status', item.status)}）\n"
            f"主办方：{item.organizer or '未提供'}\n{item.source_url}"
            for item in events
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "用法：/law status、/law scan、/law events [current|needs_review|historical|all]、"
            "/law deadlines、"
            "/law case [方向]、/law question [real|mock|random] [方向] [题型]、"
            "/law import <受控目录相对路径> [case|mock_question|real_question_candidate]、"
            "/law targets、/law plans、/law question-import <JSON路径>、"
            "/law review [来源ID]、/law review-get <ID>、/law review-status <ID> <状态>、"
            "/law bind [当前群别名]、/law bind-umo <UMO> [群别名]、"
            "/law rename <目标> <新别名>、/law publish <id> [群名]、"
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


def _split_targets(value: str) -> list[str]:
    return [
        item.strip()
        for item in value.replace("，", ",")
        .replace("、", ",")
        .replace("和", ",")
        .split(",")
        if item.strip()
    ]


def _parse_event_args(parts: list[str]) -> tuple[str, str | None, str | None]:
    status = "current"
    event_type: str | None = None
    keyword: str | None = None
    statuses = {"current", "needs_review", "historical", "all", "review"}
    event_types = {
        "competition",
        "call_for_submissions",
        "forum",
        "training",
        "internship",
        "activity",
        "moot_court",
        "conference",
        "recruitment",
    }
    for value in parts:
        candidate = str(value).strip()
        lowered = candidate.casefold()
        if lowered in statuses:
            status = "needs_review" if lowered == "review" else lowered
        elif lowered in event_types:
            event_type = lowered
        elif candidate:
            keyword = candidate if keyword is None else f"{keyword} {candidate}"
    return status, event_type, keyword


def _looks_like_umo(value: str) -> bool:
    candidate = str(value or "").strip()
    return ":" in candidate and candidate.split(":", 1)[0] in {
        "aiocqhttp",
        "qq",
        "telegram",
        "discord",
    }


def _parse_question_args(parts: list[str]) -> tuple[str, str, str | None]:
    try:
        from .content import normalize_question_type, normalize_subject
    except ImportError:
        from content import normalize_question_type, normalize_subject

    origin = "random"
    subject = ""
    question_type: str | None = None
    remaining = list(parts)
    if remaining:
        first = remaining[0].strip().lower()
        if (
            first not in {"real", "mock", "random"}
            and normalize_question_type(remaining[0]) is None
            and normalize_subject(remaining[0]) is None
        ):
            # The command syntax puts origin first. Preserve an unsupported
            # explicit value so the service can return a parameter error.
            origin = remaining.pop(0)
    for part in remaining:
        candidate = part.strip().lower()
        if candidate in {"real", "mock", "random"}:
            origin = candidate
            continue
        normalized_type = normalize_question_type(part)
        if normalized_type:
            question_type = normalized_type
            continue
        if normalize_subject(part):
            subject = part
            continue
        if not subject:
            subject = part
        else:
            question_type = part
    return origin, subject, question_type


def _parse_document_import_args(parts: list[str]) -> tuple[str, str]:
    values = list(parts)
    content_kind = "auto"
    if values and values[-1] in {
        "auto",
        "case",
        "mock_question",
        "real_question_candidate",
    }:
        content_kind = values.pop()
    return " ".join(values), content_kind


def _extract_question_import_path(message_text: str) -> str | None:
    """Read the raw import tail so Windows backslashes survive shlex parsing."""
    fields = message_text.strip().split(None, 2)
    if len(fields) < 3:
        return None
    if fields[0].lower() != "/law" or fields[1].lower() not in {
        "question-import",
        "import-questions",
    }:
        return None
    path = fields[2].strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"'}:
        path = path[1:-1]
    return path or None


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
        "radar_status": event.metadata.get("radar_status"),
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
