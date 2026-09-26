"""Validation and preview helpers for the Phase 0.4 structured-material contract.

This module deliberately has no database, network, LLM, or filesystem side
effects. It validates a UTF-8 JSON exchange document and returns a stable
preview for the separate hash-bound PDF import service without treating
declarations as verified legal identities.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

try:
    from .content import normalize_question_type
except ImportError:
    from content import normalize_question_type


SCHEMA_VERSION = "1.0"
MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_TOTAL_TEXT_CHARS = 600_000
MAX_MATERIALS = 500
MAX_QUESTIONS = 1_000
MAX_CASES = 500
MAX_SUBQUESTIONS = 100
MAX_BLOCKS_PER_ENTRY = 500
MAX_BLOCK_TEXT_CHARS = 200_000
MAX_NESTING_DEPTH = 20

BLOCK_KINDS = {
    "paragraph",
    "heading",
    "list",
    "table_text",
    "image_reference",
    "formula_text",
}
PROVENANCES = {"source_text", "external_model", "human_authored", "unknown"}
PREPARATION_METHODS = {
    "human_transcribed",
    "external_model_assisted",
    "plugin_extracted",
    "mixed",
}
REVIEW_STATUSES = {"pending_review", "needs_review", "reviewed"}
PROTECTED_IDENTITIES = {
    "verified_real_question",
    "official_case",
    "verified_official",
}
ANSWER_STATUSES = {"provided", "not_provided", "unresolved", "supplemental"}
_QUESTION_MARKER = re.compile(
    r"(?:第\s*[一二三四五六七八九十百千万\d]+\s*[题问]|"
    r"(?<![\w])\d+\s*[、.)．])"
)
_SECTION_CONTAMINATION = re.compile(r"[\[【]\s*(?:问题|答题要求|参考答案)\s*[\]】]")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    severity: str
    message: str
    item_id: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        result = {
            "code": self.code,
            "path": self.path,
            "severity": self.severity,
            "message": self.message,
        }
        if self.item_id is not None:
            result["item_id"] = self.item_id
        return result


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Side-effect-free result of validating one structured-material document."""

    payload: dict[str, Any] | None
    document: dict[str, Any]
    materials: tuple[dict[str, Any], ...]
    questions: tuple[dict[str, Any], ...]
    cases: tuple[dict[str, Any], ...]
    valid_question_items: tuple[dict[str, Any], ...]
    valid_case_items: tuple[dict[str, Any], ...]
    issues: tuple[ValidationIssue, ...]
    review_item_ids: frozenset[str]

    @property
    def fatal(self) -> bool:
        return any(issue.severity == "fatal" for issue in self.issues)

    @property
    def usable_questions(self) -> int:
        return len(self.valid_question_items) if not self.fatal else 0

    @property
    def usable_cases(self) -> int:
        return len(self.valid_case_items) if not self.fatal else 0

    @property
    def review_items(self) -> int:
        return len(self.review_item_ids)

    @property
    def entry_error_item_ids(self) -> frozenset[str]:
        return frozenset(
            issue.item_id
            for issue in self.issues
            if issue.severity == "error" and issue.item_id is not None
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "valid": not self.fatal and not self.entry_error_item_ids,
            "fatal": self.fatal,
            "schema_version": (
                self.payload.get("schema_version") if self.payload else None
            ),
            "document": copy.deepcopy(self.document),
            "materials": copy.deepcopy(list(self.materials)),
            "questions": copy.deepcopy(list(self.questions)),
            "cases": copy.deepcopy(list(self.cases)),
            "issues": [issue.to_mapping() for issue in self.issues],
        }


class _Validator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.issues: list[ValidationIssue] = []
        self.review_item_ids: set[str] = set()
        self.registered_ids: dict[str, str] = {}
        self.material_ids: set[str] = set()
        self.material_text_by_id: dict[str, str] = {}
        self.valid_questions: list[dict[str, Any]] = []
        self.valid_cases: list[dict[str, Any]] = []

    def add(
        self,
        code: str,
        path: str,
        severity: str,
        message: str,
        *,
        item_id: str | None = None,
    ) -> None:
        self.issues.append(ValidationIssue(code, path, severity, message, item_id))
        if severity == "review" and item_id is not None:
            self.review_item_ids.add(item_id)

    def register_id(self, value: Any, path: str, *, item_id: str | None = None) -> str:
        if not isinstance(value, str) or not value.strip():
            self.add(
                "id.missing", path, "error", "ID 必须是非空字符串", item_id=item_id
            )
            return ""
        normalized = value.strip()
        previous_path = self.registered_ids.get(normalized)
        if previous_path is not None:
            self.add(
                "id.duplicate",
                path,
                "fatal",
                f"ID 与 {previous_path} 重复：{normalized}",
                item_id=item_id,
            )
        else:
            self.registered_ids[normalized] = path
        return normalized

    def validate_document(self) -> dict[str, Any]:
        raw = self.payload.get("document")
        if not isinstance(raw, dict):
            self.add("root.document_type", "document", "fatal", "document 必须是对象")
            return {}
        document = copy.deepcopy(raw)
        required = (
            "title",
            "source_type",
            "original_filename",
            "original_file_sha256",
            "preparation_method",
            "verification_status",
        )
        for field in required:
            value = document.get(field)
            if not isinstance(value, str) or not value.strip():
                self.add(
                    f"document.{field}_missing",
                    f"document.{field}",
                    "error",
                    f"document.{field} 必须是非空字符串",
                )
        digest = document.get("original_file_sha256")
        if (
            isinstance(digest, str)
            and digest
            and not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
        ):
            self.add(
                "document.original_file_sha256_invalid",
                "document.original_file_sha256",
                "error",
                "原始文件 SHA-256 必须是 64 位十六进制字符串",
            )
        preparation = document.get("preparation_method")
        if isinstance(preparation, str) and preparation not in PREPARATION_METHODS:
            self.add(
                "document.preparation_method_invalid",
                "document.preparation_method",
                "error",
                "整理方式不是受支持的规范值",
            )
        status = document.get("verification_status")
        if isinstance(status, str) and status in PROTECTED_IDENTITIES:
            self.add(
                "document.protected_identity_claim",
                "document.verification_status",
                "review",
                "模板不能授予核验真题或官方案例身份",
            )
        if "source_url" in document and not isinstance(document["source_url"], str):
            self.add(
                "document.source_url_type",
                "document.source_url",
                "error",
                "source_url 必须是字符串或空字符串",
            )
        return document

    def validate_materials(self, values: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(values, list):
            self.add(
                "root.materials_type", "materials", "fatal", "materials 必须是数组"
            )
            return ()
        if len(values) > MAX_MATERIALS:
            self.add("materials.too_many", "materials", "fatal", "材料数量超过限制")
        all_items: list[dict[str, Any]] = []
        for index, raw in enumerate(values[:MAX_MATERIALS]):
            path = f"materials[{index}]"
            if not isinstance(raw, dict):
                self.add("material.type", path, "error", "材料必须是对象")
                continue
            material = copy.deepcopy(raw)
            material_id = self.register_id(material.get("id"), f"{path}.id")
            if material_id:
                self.material_ids.add(material_id)
            blocks_ok = self.validate_blocks(
                material.get("blocks"),
                f"{path}.blocks",
                item_id=material_id or None,
                required=True,
            )
            if blocks_ok:
                material["blocks"] = sorted(
                    copy.deepcopy(material["blocks"]), key=lambda block: block["order"]
                )
                combined_text = "\n".join(
                    str(block.get("text") or "") for block in material["blocks"]
                )
                if material_id:
                    self.material_text_by_id[material_id] = combined_text
                if _SECTION_CONTAMINATION.search(combined_text):
                    self.add(
                        "material.section_contamination",
                        f"{path}.blocks",
                        "review",
                        "公共材料包含问题、答题要求或答案区段标记，需重新分层",
                        item_id=material_id or None,
                    )
            if not isinstance(material.get("title", ""), str):
                self.add(
                    "material.title_type",
                    f"{path}.title",
                    "error",
                    "材料标题必须是字符串",
                    item_id=material_id or None,
                )
            if material_id:
                all_items.append(material)
            if material_id and any(
                issue.item_id == material_id and issue.severity == "review"
                for issue in self.issues
            ):
                self.review_item_ids.add(material_id)
        return tuple(all_items)

    def validate_blocks(
        self,
        values: Any,
        path: str,
        *,
        item_id: str | None,
        required: bool,
    ) -> bool:
        if values is None and not required:
            return True
        if not isinstance(values, list):
            self.add("block.type", path, "error", "内容块必须是数组", item_id=item_id)
            return False
        if required and not values:
            self.add(
                "block.missing", path, "error", "至少需要一个内容块", item_id=item_id
            )
            return False
        if len(values) > MAX_BLOCKS_PER_ENTRY:
            self.add(
                "block.too_many", path, "error", "内容块数量超过限制", item_id=item_id
            )
        orders: set[int] = set()
        valid = True
        for index, raw in enumerate(values[:MAX_BLOCKS_PER_ENTRY]):
            block_path = f"{path}[{index}]"
            if not isinstance(raw, dict):
                self.add(
                    "block.object_required",
                    block_path,
                    "error",
                    "内容块必须是对象",
                    item_id=item_id,
                )
                valid = False
                continue
            block_id = self.register_id(
                raw.get("id"), f"{block_path}.id", item_id=item_id
            )
            order = raw.get("order")
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                self.add(
                    "block.order_invalid",
                    f"{block_path}.order",
                    "error",
                    "内容块 order 必须是正整数",
                    item_id=item_id,
                )
                valid = False
            elif order in orders:
                self.add(
                    "block.order_duplicate",
                    f"{block_path}.order",
                    "error",
                    "内容块 order 不能重复",
                    item_id=item_id,
                )
                valid = False
            else:
                orders.add(order)
            kind = raw.get("kind")
            if kind not in BLOCK_KINDS:
                self.add(
                    "block.kind_invalid",
                    f"{block_path}.kind",
                    "error",
                    "内容块 kind 不受支持",
                    item_id=item_id,
                )
                valid = False
            text = raw.get("text")
            if not isinstance(text, str):
                self.add(
                    "block.text_type",
                    f"{block_path}.text",
                    "error",
                    "内容块 text 必须是字符串",
                    item_id=item_id,
                )
                valid = False
            elif not text.strip():
                self.add(
                    "block.text_missing",
                    f"{block_path}.text",
                    "error",
                    "内容块缺少实质文本或引用说明",
                    item_id=item_id,
                )
                valid = False
            elif len(text) > MAX_BLOCK_TEXT_CHARS:
                self.add(
                    "block.text_too_long",
                    f"{block_path}.text",
                    "error",
                    "单个内容块超过长度限制",
                    item_id=item_id,
                )
                valid = False
            provenance = raw.get("provenance")
            if provenance not in PROVENANCES:
                self.add(
                    "block.provenance_missing",
                    f"{block_path}.provenance",
                    "review",
                    "内容块缺少可识别的来源标记",
                    item_id=item_id,
                )
            locator = raw.get("locator")
            if not isinstance(locator, str) or not locator.strip():
                self.add(
                    "block.locator_missing",
                    f"{block_path}.locator",
                    "review",
                    "内容块缺少原文定位，需人工复核",
                    item_id=item_id,
                )
            if not block_id:
                valid = False
        if len(orders) != len(set(orders)):
            valid = False
        return valid

    def validate_questions(
        self, values: Any, *, material_ids: set[str]
    ) -> tuple[dict[str, Any], ...]:
        if not isinstance(values, list):
            self.add(
                "root.questions_type", "questions", "fatal", "questions 必须是数组"
            )
            return ()
        if len(values) > MAX_QUESTIONS:
            self.add("questions.too_many", "questions", "fatal", "题目数量超过限制")
        all_items: list[dict[str, Any]] = []
        for index, raw in enumerate(values[:MAX_QUESTIONS]):
            path = f"questions[{index}]"
            item_id, ok = self._question_header(raw, path)
            if not ok:
                continue
            question = copy.deepcopy(raw)
            question["id"] = item_id
            question_ok = True
            question_type = normalize_question_type(question.get("question_type"))
            if question_type is None:
                self.add(
                    "question.type_invalid",
                    f"{path}.question_type",
                    "error",
                    "题型缺失或不受支持",
                    item_id=item_id,
                )
                question_ok = False
            else:
                question["question_type"] = question_type
            refs = question.get("material_refs", [])
            if not isinstance(refs, list):
                self.add(
                    "question.material_refs_type",
                    f"{path}.material_refs",
                    "error",
                    "material_refs 必须是数组",
                    item_id=item_id,
                )
                question_ok = False
            else:
                for ref_index, ref in enumerate(refs):
                    if not isinstance(ref, str) or ref not in material_ids:
                        self.add(
                            "question.material_ref_missing",
                            f"{path}.material_refs[{ref_index}]",
                            "fatal",
                            "material_ref 未指向同一模板中的材料",
                            item_id=item_id,
                        )
            has_subquestions = bool(question.get("subquestions"))
            if question.get("stem_blocks") is None and has_subquestions:
                question["stem_blocks"] = []
            stem_ok = self.validate_blocks(
                question.get("stem_blocks"),
                f"{path}.stem_blocks",
                item_id=item_id,
                required=not has_subquestions,
            )
            if (
                isinstance(question.get("stem_blocks"), list)
                and question.get("stem_blocks")
                and not any(
                    isinstance(block, dict) and str(block.get("text") or "").strip()
                    for block in question["stem_blocks"]
                )
            ) or (not question.get("stem_blocks") and not has_subquestions):
                self.add(
                    "question.stem_missing",
                    f"{path}.stem_blocks",
                    "error",
                    "题目缺少实质题干",
                    item_id=item_id,
                )
            if not stem_ok:
                question_ok = False
            elif isinstance(question.get("stem_blocks"), list):
                question["stem_blocks"] = sorted(
                    copy.deepcopy(question["stem_blocks"]),
                    key=lambda block: block["order"],
                )
            options_ok = self._validate_options(question, question_type, path, item_id)
            question_ok = options_ok and question_ok
            if not self._validate_answer(question, question_type, path, item_id):
                question_ok = False
            if not self._validate_answer_requirements(
                question.get("answer_requirements", []),
                f"{path}.answer_requirements",
                item_id,
            ):
                question_ok = False
            explanation_ok = self._validate_optional_blocks(
                question.get("explanation_blocks", []),
                f"{path}.explanation_blocks",
                item_id,
            )
            question_ok = explanation_ok and question_ok
            if not self._validate_locators(
                question.get("locators"), f"{path}.locators", item_id
            ):
                question_ok = False
            sub_ok = self._validate_subquestions(question, path, item_id)
            question_ok = sub_ok and question_ok
            self._check_material_stem_duplication(question, path, item_id)
            if not self._validate_review_identity(
                question, f"{path}.verification_status", item_id, "question"
            ):
                question_ok = False
            if self._suspected_merged(question):
                self.add(
                    "question.suspected_merged",
                    f"{path}.stem_blocks",
                    "review",
                    "题干疑似包含多个独立题号，需人工确认边界",
                    item_id=item_id,
                )
            question.pop("_option_keys", None)
            all_items.append(question)
            if question_ok:
                self.valid_questions.append(question)
        return tuple(all_items)

    def _question_header(self, raw: Any, path: str) -> tuple[str, bool]:
        if not isinstance(raw, dict):
            self.add("question.type", path, "error", "题目必须是对象")
            return "", False
        item_id = self.register_id(raw.get("id"), f"{path}.id")
        ok = bool(item_id)
        source_number = raw.get("source_number")
        if not isinstance(source_number, str) or not source_number.strip():
            self.add(
                "question.source_number_missing",
                f"{path}.source_number",
                "error",
                "题目必须保留原始题号",
                item_id=item_id or None,
            )
            ok = False
        return item_id, ok

    def _validate_options(
        self,
        question: dict[str, Any],
        question_type: str | None,
        path: str,
        item_id: str,
    ) -> bool:
        values = question.get("options", [])
        if values is None:
            values = []
            question["options"] = values
        if not isinstance(values, list):
            self.add(
                "question.options_type",
                f"{path}.options",
                "error",
                "options 必须是数组",
                item_id=item_id,
            )
            return False
        if (
            question_type
            in {
                "single_choice",
                "multiple_choice",
                "indefinite_choice",
            }
            and len(values) < 2
        ):
            self.add(
                "question.options_missing",
                f"{path}.options",
                "error",
                "选择题至少需要两个选项",
                item_id=item_id,
            )
            return False
        keys: set[str] = set()
        ok = True
        for index, option in enumerate(values):
            option_path = f"{path}.options[{index}]"
            if not isinstance(option, dict):
                self.add(
                    "question.option_type",
                    option_path,
                    "error",
                    "选项必须是对象",
                    item_id=item_id,
                )
                ok = False
                continue
            key = option.get("key")
            normalized_key = str(key or "").strip().upper()
            if not normalized_key:
                self.add(
                    "question.option_key_missing",
                    f"{option_path}.key",
                    "error",
                    "选项缺少 key",
                    item_id=item_id,
                )
                ok = False
            elif normalized_key in keys:
                self.add(
                    "question.option_key_duplicate",
                    f"{option_path}.key",
                    "error",
                    "选项 key 不能重复",
                    item_id=item_id,
                )
                ok = False
            else:
                keys.add(normalized_key)
            if not isinstance(option.get("text"), str) or not option["text"].strip():
                self.add(
                    "question.option_text_missing",
                    f"{option_path}.text",
                    "error",
                    "选项缺少正文",
                    item_id=item_id,
                )
                ok = False
        question["_option_keys"] = sorted(keys)
        return ok

    def _validate_answer(
        self,
        question: dict[str, Any],
        question_type: str | None,
        path: str,
        item_id: str,
    ) -> bool:
        answer = question.get("answer")
        status = question.get("answer_status")
        reason = question.get("answer_reason")
        if status is not None and status not in ANSWER_STATUSES:
            self.add(
                "question.answer_status_invalid",
                f"{path}.answer_status",
                "error",
                "答案状态不受支持",
                item_id=item_id,
            )
            return False
        if answer is None:
            if status == "provided" and _all_subquestions_answered(question):
                return True
            if (
                status not in {"not_provided", "unresolved", "supplemental"}
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                self.add(
                    "question.answer_reason_missing",
                    f"{path}.answer_reason",
                    "error",
                    "答案为空时必须说明未提供或无法确定的原因",
                    item_id=item_id,
                )
                return False
            self.add(
                "question.answer_missing",
                f"{path}.answer",
                "review",
                "原文没有可核验参考答案，需人工复核",
                item_id=item_id,
            )
            return True
        if status not in {None, "provided", "supplemental"}:
            self.add(
                "question.answer_status_conflict",
                f"{path}.answer_status",
                "error",
                "答案内容与答案状态冲突",
                item_id=item_id,
            )
            return False
        provenance = answer.get("provenance") if isinstance(answer, dict) else None
        if provenance not in PROVENANCES:
            self.add(
                "question.answer_provenance_missing",
                f"{path}.answer.provenance",
                "review",
                "答案缺少来源标记",
                item_id=item_id,
            )
        elif provenance != "source_text":
            self.add(
                "question.answer_not_source",
                f"{path}.answer.provenance",
                "review",
                "答案不是原始资料答案，不能视为官方参考答案",
                item_id=item_id,
            )
        if (
            isinstance(answer, dict)
            and isinstance(answer.get("blocks"), list)
            and not self.validate_blocks(
                answer["blocks"],
                f"{path}.answer.blocks",
                item_id=item_id,
                required=True,
            )
        ):
            return False
        if question_type in {"single_choice", "multiple_choice", "indefinite_choice"}:
            keys = _answer_keys(answer)
            option_keys = set(question.get("_option_keys", []))
            if not keys:
                self.add(
                    "question.answer_key_missing",
                    f"{path}.answer",
                    "error",
                    "选择题答案必须包含选项键",
                    item_id=item_id,
                )
                return False
            if not keys.issubset(option_keys):
                self.add(
                    "question.answer_key_unknown",
                    f"{path}.answer",
                    "error",
                    "答案包含不存在的选项键",
                    item_id=item_id,
                )
                return False
            if question_type == "single_choice" and len(keys) != 1:
                self.add(
                    "question.answer_single_count",
                    f"{path}.answer",
                    "error",
                    "单选题只能有一个正确选项",
                    item_id=item_id,
                )
                return False
        elif question_type == "true_false" and _answer_boolean(answer) is None:
            self.add(
                "question.answer_boolean_invalid",
                f"{path}.answer",
                "error",
                "判断题答案必须明确为 true/false 或对/错",
                item_id=item_id,
            )
            return False
        return True

    def _validate_answer_requirements(
        self, values: Any, path: str, item_id: str
    ) -> bool:
        if values in (None, []):
            return True
        if not isinstance(values, list):
            self.add(
                "answer_requirement.type",
                path,
                "error",
                "answer_requirements 必须是数组",
                item_id=item_id,
            )
            return False
        ok = True
        seen_orders: set[int] = set()
        for index, requirement in enumerate(values):
            requirement_path = f"{path}[{index}]"
            if not isinstance(requirement, dict):
                self.add(
                    "answer_requirement.object_required",
                    requirement_path,
                    "error",
                    "答题要求必须是对象",
                    item_id=item_id,
                )
                ok = False
                continue
            order = requirement.get("order")
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                self.add(
                    "answer_requirement.order_invalid",
                    f"{requirement_path}.order",
                    "error",
                    "答题要求 order 必须是正整数",
                    item_id=item_id,
                )
                ok = False
            elif order in seen_orders:
                self.add(
                    "answer_requirement.order_duplicate",
                    f"{requirement_path}.order",
                    "error",
                    "答题要求 order 不能重复",
                    item_id=item_id,
                )
                ok = False
            else:
                seen_orders.add(order)
            text = requirement.get("text")
            if not isinstance(text, str) or not text.strip():
                self.add(
                    "answer_requirement.text_missing",
                    f"{requirement_path}.text",
                    "error",
                    "答题要求必须包含非空 text",
                    item_id=item_id,
                )
                ok = False
            elif len(text) > MAX_BLOCK_TEXT_CHARS:
                self.add(
                    "answer_requirement.text_too_long",
                    f"{requirement_path}.text",
                    "error",
                    "单条答题要求超过长度限制",
                    item_id=item_id,
                )
                ok = False
            locator = requirement.get("locator")
            if not isinstance(locator, str) or not locator.strip():
                self.add(
                    "answer_requirement.locator_missing",
                    f"{requirement_path}.locator",
                    "review",
                    "答题要求缺少原文定位，需人工复核",
                    item_id=item_id,
                )
            provenance = requirement.get("provenance")
            if provenance not in PROVENANCES:
                self.add(
                    "answer_requirement.provenance_missing",
                    f"{requirement_path}.provenance",
                    "review",
                    "答题要求缺少可识别的来源标记",
                    item_id=item_id,
                )
        values.sort(
            key=lambda item: item.get("order", 0) if isinstance(item, dict) else 0
        )
        return ok

    def _check_material_stem_duplication(
        self, question: dict[str, Any], path: str, item_id: str
    ) -> None:
        stem_blocks = question.get("stem_blocks", [])
        material_refs = question.get("material_refs", [])
        if not isinstance(stem_blocks, list) or not isinstance(material_refs, list):
            return
        stem_text = "\n".join(
            str(block.get("text") or "")
            for block in stem_blocks
            if isinstance(block, dict)
        )
        normalized_stem = _comparison_text(stem_text)
        if len(normalized_stem) < 60:
            return
        for material_ref in material_refs:
            material_text = self.material_text_by_id.get(str(material_ref), "")
            normalized_material = _comparison_text(material_text)
            if not normalized_material:
                continue
            shorter, longer = sorted((normalized_stem, normalized_material), key=len)
            common_prefix = 0
            for left, right in zip(normalized_stem, normalized_material):
                if left != right:
                    break
                common_prefix += 1
            if shorter == longer or (
                len(shorter) >= 60 and common_prefix / len(shorter) >= 0.8
            ):
                self.add(
                    "question.material_stem_duplicate",
                    f"{path}.stem_blocks",
                    "review",
                    "题干与其引用的公共材料高度重复，需确认是否重复保存",
                    item_id=item_id,
                )
                return

    def _validate_optional_blocks(self, values: Any, path: str, item_id: str) -> bool:
        if values in (None, []):
            return True
        ok = self.validate_blocks(values, path, item_id=item_id, required=False)
        if ok and isinstance(values, list):
            values.sort(key=lambda block: block["order"])
        return ok

    def _validate_subquestions(
        self, question: dict[str, Any], path: str, item_id: str
    ) -> bool:
        values = question.get("subquestions", [])
        if values in (None, []):
            return True
        if not isinstance(values, list):
            self.add(
                "question.subquestions_type",
                f"{path}.subquestions",
                "error",
                "subquestions 必须是数组",
                item_id=item_id,
            )
            return False
        if len(values) > MAX_SUBQUESTIONS:
            self.add(
                "question.subquestions_too_many",
                f"{path}.subquestions",
                "error",
                "小问数量超过限制",
                item_id=item_id,
            )
            return False
        ok = True
        requirement_like: list[bool] = []
        for index, subquestion in enumerate(values):
            sub_path = f"{path}.subquestions[{index}]"
            if not isinstance(subquestion, dict):
                self.add(
                    "subquestion.type",
                    sub_path,
                    "error",
                    "小问必须是对象",
                    item_id=item_id,
                )
                ok = False
                continue
            sub_id = self.register_id(
                subquestion.get("id"), f"{sub_path}.id", item_id=item_id
            )
            if not sub_id:
                ok = False
            if (
                not isinstance(subquestion.get("source_number"), str)
                or not subquestion["source_number"].strip()
            ):
                self.add(
                    "subquestion.source_number_missing",
                    f"{sub_path}.source_number",
                    "error",
                    "小问必须保留原始编号",
                    item_id=item_id,
                )
                ok = False
            if not self.validate_blocks(
                subquestion.get("stem_blocks"),
                f"{sub_path}.stem_blocks",
                item_id=item_id,
                required=True,
            ):
                ok = False
            else:
                subquestion["stem_blocks"] = sorted(
                    copy.deepcopy(subquestion["stem_blocks"]),
                    key=lambda block: block["order"],
                )
            if not self._validate_optional_blocks(
                subquestion.get("explanation_blocks", []),
                f"{sub_path}.explanation_blocks",
                item_id,
            ):
                ok = False
            if not self._validate_answer_requirements(
                subquestion.get("answer_requirements", []),
                f"{sub_path}.answer_requirements",
                item_id,
            ):
                ok = False
            if not self._validate_locators(
                subquestion.get("locators"), f"{sub_path}.locators", item_id
            ):
                ok = False
            sub_answer = subquestion.get("answer")
            if sub_answer is None:
                reason = subquestion.get("answer_reason")
                if not isinstance(reason, str) or not reason.strip():
                    self.add(
                        "subquestion.answer_reason_missing",
                        f"{sub_path}.answer_reason",
                        "error",
                        "小问答案为空时必须说明原因",
                        item_id=item_id,
                    )
                    ok = False
                else:
                    self.add(
                        "subquestion.answer_missing",
                        f"{sub_path}.answer",
                        "review",
                        "小问没有可核验参考答案",
                        item_id=item_id,
                    )
            elif (
                isinstance(sub_answer, dict)
                and sub_answer.get("provenance") not in PROVENANCES
            ):
                self.add(
                    "subquestion.answer_provenance_missing",
                    f"{sub_path}.answer.provenance",
                    "review",
                    "小问答案缺少来源标记",
                    item_id=item_id,
                )
            stem_text = "\n".join(
                str(block.get("text") or "")
                for block in subquestion.get("stem_blocks", [])
                if isinstance(block, dict)
            )
            requirement_like.append(_looks_like_answer_requirement(stem_text))
        grouped_requirement_sequence = (
            len(values) >= 2
            and sum(requirement_like) >= 2
            and all(
                not re.search(r"[?？]|(?:如何|是否|为什么|请分析|请说明|请判断)", text)
                and len(text.strip()) <= 160
                for subquestion in values
                if isinstance(subquestion, dict)
                for text in [
                    "\n".join(
                        str(block.get("text") or "")
                        for block in subquestion.get("stem_blocks", [])
                        if isinstance(block, dict)
                    )
                ]
            )
        )
        for index, likely in enumerate(requirement_like):
            if likely or grouped_requirement_sequence:
                self.add(
                    "subquestion.possible_answer_requirement",
                    f"{path}.subquestions[{index}].stem_blocks",
                    "review",
                    "小问文本呈现答题规范或评分要求特征，需确认是否应改为 answer_requirements",
                    item_id=item_id,
                )
        return ok

    def _validate_locators(self, values: Any, path: str, item_id: str) -> bool:
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value.strip() for value in values)
        ):
            self.add(
                "locator.missing",
                path,
                "review",
                "缺少可追溯原文定位，需人工复核",
                item_id=item_id,
            )
            return False
        return True

    def _validate_review_identity(
        self, item: dict[str, Any], path: str, item_id: str, kind: str
    ) -> bool:
        status = item.get("verification_status")
        if status is None:
            self.add(
                f"{kind}.verification_status_missing",
                path,
                "review",
                "真实性状态缺失，需人工复核",
                item_id=item_id,
            )
            return True
        if not isinstance(status, str):
            self.add(
                f"{kind}.verification_status_type",
                path,
                "error",
                "真实性状态必须是字符串",
                item_id=item_id,
            )
            return False
        if status not in REVIEW_STATUSES and status not in PROTECTED_IDENTITIES:
            self.add(
                f"{kind}.verification_status_invalid",
                path,
                "error",
                "真实性状态不是受支持的规范值",
                item_id=item_id,
            )
            return False
        if status in PROTECTED_IDENTITIES:
            self.add(
                f"{kind}.protected_identity_claim",
                path,
                "review",
                "模板不能授予受保护身份",
                item_id=item_id,
            )
        return True

    def _suspected_merged(self, question: dict[str, Any]) -> bool:
        blocks = question.get("stem_blocks") or []
        text = "\n".join(
            str(block.get("text", "")) for block in blocks if isinstance(block, dict)
        )
        return len(_QUESTION_MARKER.findall(text)) >= 2

    def validate_cases(self, values: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(values, list):
            self.add("root.cases_type", "cases", "fatal", "cases 必须是数组")
            return ()
        if len(values) > MAX_CASES:
            self.add("cases.too_many", "cases", "fatal", "案例数量超过限制")
        all_items: list[dict[str, Any]] = []
        block_fields = (
            "blocks",
            "basic_facts_blocks",
            "issues_blocks",
            "holding_blocks",
            "result_blocks",
            "learning_points_blocks",
        )
        for index, raw in enumerate(values[:MAX_CASES]):
            path = f"cases[{index}]"
            if not isinstance(raw, dict):
                self.add("case.type", path, "error", "案例必须是对象")
                continue
            case = copy.deepcopy(raw)
            item_id = self.register_id(case.get("id"), f"{path}.id")
            ok = bool(item_id)
            if not isinstance(case.get("title"), str) or not case["title"].strip():
                self.add(
                    "case.title_missing",
                    f"{path}.title",
                    "error",
                    "案例必须有标题",
                    item_id=item_id or None,
                )
                ok = False
            block_count = 0
            for field in block_fields:
                if field not in case:
                    continue
                values_for_field = case[field]
                if values_for_field in (None, []):
                    continue
                if not self.validate_blocks(
                    values_for_field,
                    f"{path}.{field}",
                    item_id=item_id or None,
                    required=False,
                ):
                    ok = False
                elif isinstance(values_for_field, list):
                    case[field] = sorted(
                        copy.deepcopy(values_for_field),
                        key=lambda block: block["order"],
                    )
                    block_count += len(values_for_field)
            if block_count == 0:
                self.add(
                    "case.blocks_missing",
                    path,
                    "error",
                    "案例至少需要一组有序内容块",
                    item_id=item_id or None,
                )
                ok = False
            if not self._validate_locators(
                case.get("locators"), f"{path}.locators", item_id
            ):
                ok = False
            authority = str(case.get("authority") or "")
            if not self._validate_review_identity(
                case, f"{path}.verification_status", item_id, "case"
            ):
                ok = False
            if any(name in authority for name in ("最高人民法院", "最高人民检察院")):
                self.add(
                    "case.protected_identity_claim",
                    f"{path}.verification_status",
                    "review",
                    "模板中的权威机关或状态声明不能自动授予官方案例身份",
                    item_id=item_id,
                )
            case["identity_granted"] = False
            all_items.append(case)
            if ok:
                self.valid_cases.append(case)
        return tuple(all_items)


def validate_structured_material(
    payload: str | bytes | bytearray | Mapping[str, Any],
) -> ValidationResult:
    """Parse and validate one v1 structured-material exchange document."""

    parsed, early_issues = _decode_payload(payload)
    if parsed is None:
        return ValidationResult(
            payload=None,
            document={},
            materials=(),
            questions=(),
            cases=(),
            valid_question_items=(),
            valid_case_items=(),
            issues=tuple(early_issues),
            review_item_ids=frozenset(),
        )
    if _exceeds_depth(parsed, 0):
        early_issues.append(
            ValidationIssue("json.too_deep", "$", "fatal", "JSON 嵌套深度超过限制")
        )
    if _total_text_chars(parsed) > MAX_TOTAL_TEXT_CHARS:
        early_issues.append(
            ValidationIssue("json.too_large", "$", "fatal", "JSON 文本总长度超过限制")
        )
    if early_issues:
        return ValidationResult(
            payload=copy.deepcopy(parsed),
            document=copy.deepcopy(parsed.get("document", {}))
            if isinstance(parsed.get("document"), dict)
            else {},
            materials=(),
            questions=(),
            cases=(),
            valid_question_items=(),
            valid_case_items=(),
            issues=tuple(early_issues),
            review_item_ids=frozenset(),
        )
    if not isinstance(parsed, dict):
        return ValidationResult(
            payload=None,
            document={},
            materials=(),
            questions=(),
            cases=(),
            valid_question_items=(),
            valid_case_items=(),
            issues=(
                ValidationIssue("root.type", "$", "fatal", "JSON 根节点必须是对象"),
            ),
            review_item_ids=frozenset(),
        )
    issues: list[ValidationIssue] = []
    schema_version = parsed.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        issues.append(
            ValidationIssue(
                "schema.missing_version",
                "schema_version",
                "fatal",
                "必须提供 schema_version",
            )
        )
    elif schema_version != SCHEMA_VERSION:
        issues.append(
            ValidationIssue(
                "schema.unsupported_version",
                "schema_version",
                "fatal",
                f"不支持的结构化资料版本：{schema_version}",
            )
        )
    if issues:
        return ValidationResult(
            copy.deepcopy(parsed), {}, (), (), (), (), (), tuple(issues), frozenset()
        )
    validator = _Validator(parsed)
    document = validator.validate_document()
    materials = validator.validate_materials(parsed.get("materials", []))
    questions = validator.validate_questions(
        parsed.get("questions", []), material_ids=validator.material_ids
    )
    cases = validator.validate_cases(parsed.get("cases", []))
    return ValidationResult(
        payload=copy.deepcopy(parsed),
        document=document,
        materials=materials,
        questions=questions,
        cases=cases,
        valid_question_items=tuple(validator.valid_questions),
        valid_case_items=tuple(validator.valid_cases),
        issues=tuple(validator.issues),
        review_item_ids=frozenset(validator.review_item_ids),
    )


def build_structured_material_preview(
    payload: str | bytes | bytearray | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a deterministic, JSON-serializable preview without persistence."""

    result = validate_structured_material(payload)
    entry_error_ids = result.entry_error_item_ids
    question_summaries = [
        _question_summary(item, result.review_item_ids, item_index)
        for item_index, item in enumerate(result.questions)
    ]
    case_summaries = [
        _case_summary(item, result.review_item_ids, item_index)
        for item_index, item in enumerate(result.cases)
    ]
    subquestions = [
        subquestion
        for question in result.questions
        for subquestion in (
            question.get("subquestions")
            if isinstance(question.get("subquestions"), list)
            else []
        )
        if isinstance(subquestion, dict)
    ]
    resolved_mappings = sum(
        _has_answer_content(subquestion.get("answer")) for subquestion in subquestions
    )
    answer_requirement_count = sum(
        _list_count(question.get("answer_requirements"))
        + sum(
            _list_count(subquestion.get("answer_requirements"))
            for subquestion in (
                question.get("subquestions")
                if isinstance(question.get("subquestions"), list)
                else []
            )
            if isinstance(subquestion, dict)
        )
        for question in result.questions
    )
    issue_codes = [issue.code for issue in result.issues]
    return {
        "schema_version": (
            result.payload.get("schema_version") if result.payload else None
        ),
        "document": copy.deepcopy(result.document),
        "counts": {
            "questions": len(result.questions),
            "cases": len(result.cases),
            "materials": len(result.materials),
            "subquestions": len(subquestions),
            "answer_requirements": answer_requirement_count,
            "answer_mappings_resolved": resolved_mappings,
            "answer_mappings_unresolved": len(subquestions) - resolved_mappings,
            "duplicate_warnings": issue_codes.count("question.material_stem_duplicate"),
            "contamination_warnings": issue_codes.count(
                "material.section_contamination"
            ),
            "processable": result.usable_questions + result.usable_cases,
            "entry_errors": len(entry_error_ids),
            "review_items": result.review_items,
        },
        "materials": [
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "block_count": len(item.get("blocks", [])),
                "review_status": "pending_review"
                if item.get("id") in result.review_item_ids
                else "ready",
            }
            for item in result.materials
        ],
        "questions": question_summaries,
        "cases": case_summaries,
        "issues": [issue.to_mapping() for issue in result.issues],
    }


def _question_summary(
    item: dict[str, Any], review_ids: frozenset[str], index: int
) -> dict[str, Any]:
    item_id = item.get("id")
    return {
        "index": index,
        "id": item_id,
        "title": item.get("title", ""),
        "subjects": copy.deepcopy(item.get("subjects", [])),
        "source_number": item.get("source_number", ""),
        "question_type": item.get("question_type", ""),
        "material_refs": copy.deepcopy(item.get("material_refs", [])),
        "stem_block_count": len(item.get("stem_blocks", [])),
        "explanation_block_count": len(item.get("explanation_blocks", [])),
        "subquestion_count": _list_count(item.get("subquestions")),
        "answer_requirement_count": _list_count(item.get("answer_requirements"))
        + sum(
            _list_count(subquestion.get("answer_requirements"))
            for subquestion in (
                item.get("subquestions")
                if isinstance(item.get("subquestions"), list)
                else []
            )
            if isinstance(subquestion, dict)
        ),
        "answer_mappings_resolved": sum(
            _has_answer_content(subquestion.get("answer"))
            for subquestion in (
                item.get("subquestions")
                if isinstance(item.get("subquestions"), list)
                else []
            )
            if isinstance(subquestion, dict)
        ),
        "answer_mappings_unresolved": sum(
            not _has_answer_content(subquestion.get("answer"))
            for subquestion in (
                item.get("subquestions")
                if isinstance(item.get("subquestions"), list)
                else []
            )
            if isinstance(subquestion, dict)
        ),
        "answer_status": item.get("answer_status")
        or ("provided" if item.get("answer") is not None else "not_provided"),
        "answer_provenance": (
            item.get("answer", {}).get("provenance")
            if isinstance(item.get("answer"), dict)
            else None
        ),
        "locators": copy.deepcopy(item.get("locators", [])),
        "review_status": "pending_review" if item_id in review_ids else "ready",
        "identity_granted": False,
    }


def _case_summary(
    item: dict[str, Any], review_ids: frozenset[str], index: int
) -> dict[str, Any]:
    block_fields = (
        "blocks",
        "basic_facts_blocks",
        "issues_blocks",
        "holding_blocks",
        "result_blocks",
        "learning_points_blocks",
    )
    block_count = sum(
        len(item.get(field, []))
        for field in block_fields
        if isinstance(item.get(field), list)
    )
    item_id = item.get("id")
    return {
        "index": index,
        "id": item_id,
        "title": item.get("title", ""),
        "subjects": copy.deepcopy(item.get("subjects", [])),
        "block_count": block_count,
        "locators": copy.deepcopy(item.get("locators", [])),
        "review_status": "pending_review" if item_id in review_ids else "ready",
        "identity_granted": False,
    }


def _decode_payload(
    payload: Any,
) -> tuple[dict[str, Any] | list[Any] | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if isinstance(payload, Mapping):
        parsed: Any = copy.deepcopy(dict(payload))
    elif isinstance(payload, (bytes, bytearray, str)):
        raw = (
            bytes(payload)
            if isinstance(payload, (bytes, bytearray))
            else payload.encode("utf-8")
        )
        if len(raw) > MAX_JSON_BYTES:
            return None, [
                ValidationIssue("json.too_large", "$", "fatal", "JSON 文件超过大小限制")
            ]
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, [
                ValidationIssue("json.invalid", "$", "fatal", f"JSON 无法解析：{exc}")
            ]
    else:
        return None, [
            ValidationIssue("json.type", "$", "fatal", "输入必须是 UTF-8 JSON 或对象")
        ]
    try:
        encoded_size = len(
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError) as exc:
        return None, [
            ValidationIssue("json.invalid", "$", "fatal", f"JSON 数据不可序列化：{exc}")
        ]
    if encoded_size > MAX_JSON_BYTES:
        issues.append(
            ValidationIssue("json.too_large", "$", "fatal", "JSON 文件超过大小限制")
        )
    return parsed, issues


def _exceeds_depth(value: Any, depth: int) -> bool:
    if depth > MAX_NESTING_DEPTH:
        return True
    if isinstance(value, Mapping):
        return any(_exceeds_depth(child, depth + 1) for child in value.values())
    if isinstance(value, list):
        return any(_exceeds_depth(child, depth + 1) for child in value)
    return False


def _total_text_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_total_text_chars(child) for child in value.values())
    if isinstance(value, list):
        return sum(_total_text_chars(child) for child in value)
    return 0


def _answer_keys(answer: Any) -> set[str]:
    value = answer
    if isinstance(answer, dict):
        for key in ("keys", "correct_keys", "answers", "key", "value"):
            if key in answer:
                value = answer[key]
                break
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = re.split(r"[,，、/\\\s]+", str(value).strip())
    return {str(item).strip().upper() for item in values if str(item).strip()}


def _answer_boolean(answer: Any) -> bool | None:
    value = answer.get("value") if isinstance(answer, dict) else answer
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "正确", "对", "是", "√"}:
        return True
    if normalized in {"false", "错误", "错", "否", "×"}:
        return False
    return None


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "Z"))
    )


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _has_answer_content(answer: Any) -> bool:
    if not isinstance(answer, dict):
        return False
    if isinstance(answer.get("blocks"), list):
        return any(
            isinstance(block, dict) and str(block.get("text") or "").strip()
            for block in answer["blocks"]
        )
    return any(
        key in answer and answer[key] not in (None, "", [], {})
        for key in ("keys", "correct_keys", "answers", "key", "value", "text")
    )


def _all_subquestions_answered(question: dict[str, Any]) -> bool:
    subquestions = question.get("subquestions")
    return (
        isinstance(subquestions, list)
        and bool(subquestions)
        and all(
            isinstance(subquestion, dict)
            and _has_answer_content(subquestion.get("answer"))
            for subquestion in subquestions
        )
    )


def _looks_like_answer_requirement(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized or re.search(r"[?？]", normalized):
        return False
    signals = (
        re.search(
            r"(?:不少于|不得少于|不得超过|总字数|字数|字以内|字以上)", normalized
        ),
        re.search(r"(?:不得分|不计分|得分|计分|评分|满分)", normalized),
        re.search(
            r"(?:作答|答题|回答|表达|表述).{0,12}(?:要求|正确|完整|准确|规范|不得|应当|必须)",
            normalized,
        ),
        re.search(
            r"(?:观点|论述|理由|结论).{0,12}(?:正确|完整|准确|清晰|不得|应当|必须)",
            normalized,
        ),
        re.search(r"(?:无观点|照搬材料|不符合要求|未按要求)", normalized),
        re.search(
            r"(?:不得|必须|应当).{0,16}(?:观点|论述|材料|作答|表述|字|分)", normalized
        ),
    )
    return sum(signal is not None for signal in signals) >= 2


def match_numbered_answer_entries(
    question_numbers: list[int | str],
    question_stems: list[str],
    answer_entries: list[Mapping[str, Any]],
) -> dict[int, int]:
    """Map source-numbered answer headings to questions without semantic guessing.

    A complete one-to-one numbered sequence is mapped by position. Otherwise,
    an answer entry is accepted only when its same-number heading has one unique
    long exact-text prefix match to that question. Returned indexes are zero-based.
    """

    if len(question_numbers) != len(question_stems):
        return {}
    try:
        normalized_numbers = [int(number) for number in question_numbers]
        candidate_numbers = [int(entry.get("number")) for entry in answer_entries]
    except (TypeError, ValueError):
        return {}
    if candidate_numbers == normalized_numbers:
        return {index: index for index in range(len(normalized_numbers))}

    def normalize(value: str) -> str:
        text = unicodedata.normalize("NFKC", value)
        return "".join(
            character
            for character in text
            if not character.isspace()
            and not unicodedata.category(character).startswith(("P", "Z"))
        )

    def match_score(left: str, right: str) -> int:
        best = 0
        for left_skip in range(min(4, len(left)) + 1):
            for right_skip in range(min(4, len(right)) + 1):
                length = 0
                for left_char, right_char in zip(left[left_skip:], right[right_skip:]):
                    if left_char != right_char:
                        break
                    length += 1
                best = max(best, length)
        return best

    mapping: dict[int, int] = {}
    used_entries: set[int] = set()
    for question_index, (number, stem) in enumerate(
        zip(normalized_numbers, question_stems)
    ):
        target = normalize(stem)
        candidates = [
            (index, answer_entries[index])
            for index, candidate_number in enumerate(candidate_numbers)
            if candidate_number == number and index not in used_entries
        ]
        scores = [
            (match_score(target, normalize(str(entry.get("text") or ""))), index)
            for index, entry in candidates
        ]
        if not scores:
            continue
        best_score = max(score for score, _ in scores)
        best_indexes = [index for score, index in scores if score == best_score]
        if best_score >= 12 and len(best_indexes) == 1:
            mapping[question_index] = best_indexes[0]
            used_entries.add(best_indexes[0])
    return mapping


__all__ = [
    "BLOCK_KINDS",
    "MAX_BLOCK_TEXT_CHARS",
    "MAX_JSON_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_TOTAL_TEXT_CHARS",
    "SCHEMA_VERSION",
    "ValidationIssue",
    "ValidationResult",
    "build_structured_material_preview",
    "match_numbered_answer_entries",
    "validate_structured_material",
]
