from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SUBJECT_LABELS: dict[str, str] = {
    "criminal_law": "刑法",
    "criminal_procedure": "刑事诉讼法",
    "civil_law": "民法",
    "commercial_law": "商法",
    "civil_procedure": "民事诉讼法",
    "intellectual_property": "知识产权",
    "constitutional_administrative": "宪法与行政法",
    "civil_commercial": "民商法",
    "judicial_practice": "司法实务",
    "economic_law": "经济法",
    "other": "其他",
}

_SUBJECT_ALIASES: dict[str, str] = {
    "刑法": "criminal_law",
    "刑事法": "criminal_law",
    "刑诉": "criminal_procedure",
    "刑事诉讼": "criminal_procedure",
    "刑事诉讼法": "criminal_procedure",
    "民法": "civil_law",
    "商法": "commercial_law",
    "民诉": "civil_procedure",
    "民事诉讼": "civil_procedure",
    "民事诉讼法": "civil_procedure",
    "知产": "intellectual_property",
    "知识产权法": "intellectual_property",
    "知识产权": "intellectual_property",
    "宪法": "constitutional_administrative",
    "行政法": "constitutional_administrative",
    "宪法与行政法": "constitutional_administrative",
    "民商": "civil_commercial",
    "民商法": "civil_commercial",
    "司法实务": "judicial_practice",
    "实务": "judicial_practice",
    "经济法": "economic_law",
    "其他": "other",
}

QUESTION_TYPE_LABELS: dict[str, str] = {
    "single_choice": "单项选择题",
    "multiple_choice": "多项选择题",
    "true_false": "判断题",
    "short_answer": "简答题",
    "case_analysis": "案例分析题",
}

_QUESTION_TYPE_ALIASES: dict[str, str] = {
    "单选": "single_choice",
    "单选题": "single_choice",
    "single": "single_choice",
    "single_choice": "single_choice",
    "多选": "multiple_choice",
    "多选题": "multiple_choice",
    "multiple": "multiple_choice",
    "multiple_choice": "multiple_choice",
    "判断": "true_false",
    "判断题": "true_false",
    "true_false": "true_false",
    "简答": "short_answer",
    "简答题": "short_answer",
    "short_answer": "short_answer",
    "案例": "case_analysis",
    "案例分析": "case_analysis",
    "案例分析题": "case_analysis",
    "case_analysis": "case_analysis",
}

QUESTION_ORIGINS = {"real", "mock", "random"}
SELECTION_MODES = {"random", "fixed", "rotation"}


def normalize_subject(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate or candidate in {"random", "随机", "任意"}:
        return None
    if candidate in SUBJECT_LABELS:
        return candidate
    return _SUBJECT_ALIASES.get(candidate)


def subject_filter(value: Any) -> frozenset[str] | None:
    subject = normalize_subject(value)
    if subject is None:
        return None
    if subject == "civil_commercial":
        return frozenset({"civil_commercial", "civil_law", "commercial_law"})
    if subject == "criminal_law":
        return frozenset({"criminal_law"})
    return frozenset({subject})


def subject_matches(subjects: tuple[str, ...] | list[str], requested: Any) -> bool:
    requested_subjects = subject_filter(requested)
    if requested_subjects is None:
        return True
    actual = {normalize_subject(item) for item in subjects}
    return bool(actual & requested_subjects)


def normalize_question_type(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate or candidate in {"random", "随机", "任意"}:
        return None
    return _QUESTION_TYPE_ALIASES.get(candidate)


def normalize_origin(value: Any) -> str:
    candidate = str(value or "random").strip().lower()
    return candidate if candidate in QUESTION_ORIGINS else "random"


def normalize_selection_mode(value: Any) -> str:
    candidate = str(value or "random").strip().lower()
    return candidate if candidate in SELECTION_MODES else "random"


def parse_subject(value: Any, *, field_name: str = "方向") -> str | None:
    """Normalize an explicit subject, rejecting unknown values."""
    candidate = str(value or "").strip()
    if not candidate or candidate.lower() in {"random", "随机", "任意"}:
        return None
    normalized = normalize_subject(candidate)
    if normalized is None:
        raise ValueError(f"不支持的{field_name}：{candidate}")
    return normalized


def parse_question_type(value: Any, *, field_name: str = "题型") -> str | None:
    """Normalize an explicit question type, rejecting unknown values."""
    candidate = str(value or "").strip()
    if not candidate or candidate.lower() in {"random", "随机", "任意"}:
        return None
    normalized = normalize_question_type(candidate)
    if normalized is None:
        raise ValueError(f"不支持的{field_name}：{candidate}")
    return normalized


def parse_origin(value: Any) -> str:
    """Normalize an explicit question origin, rejecting unknown values."""
    candidate = str(value or "random").strip().lower()
    if not candidate or candidate in {"random", "随机", "任意"}:
        return "random"
    if candidate not in QUESTION_ORIGINS:
        raise ValueError(f"不支持的题目来源：{candidate}；可选值为 real、mock、random")
    return candidate


def parse_selection_mode(value: Any) -> str:
    """Normalize an explicit plan mode, rejecting unknown values."""
    candidate = str(value or "random").strip().lower()
    if not candidate or candidate in {"random", "随机", "任意"}:
        return "random"
    if candidate not in SELECTION_MODES:
        raise ValueError(
            f"不支持的选择模式：{candidate}；可选值为 random、fixed、rotation"
        )
    return candidate


@dataclass(frozen=True, slots=True)
class QuestionRequest:
    origin: str = "random"
    subject: str | None = None
    question_type: str | None = None
    source_name: str | None = None
    exam_year: str | None = None

    @classmethod
    def from_values(
        cls,
        *,
        origin: Any = "random",
        subject: Any = None,
        question_type: Any = None,
        source_name: Any = None,
        exam_year: Any = None,
    ) -> QuestionRequest:
        return cls(
            origin=parse_origin(origin),
            subject=parse_subject(subject),
            question_type=parse_question_type(question_type),
            source_name=str(source_name).strip() if source_name else None,
            exam_year=str(exam_year).strip() if exam_year else None,
        )


@dataclass(frozen=True, slots=True)
class RealQuestion:
    source_name: str
    exam_name: str
    subject: str
    question_type: str
    stem: str
    options: Any = field(default_factory=list)
    answer: Any = None
    explanation: str = ""
    answer_source: str = "not_provided"
    verification_status: str = "verified"
    source_url: str = ""
    source_locator: str = ""
    exam_year: str = ""
    exam_date: str = ""
    paper: str = ""
    question_number: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    id: int | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> RealQuestion:
        if not isinstance(raw, dict):
            raise TypeError("real question must be an object")
        source_name = _required_text(raw, "source_name")
        exam_name = _required_text(raw, "exam_name")
        stem = _required_text(raw, "stem", aliases=("question", "text"))
        subject = normalize_subject(raw.get("subject"))
        question_type = normalize_question_type(
            raw.get("question_type", raw.get("type"))
        )
        if subject is None:
            raise ValueError("real question subject is missing or unsupported")
        if question_type is None:
            raise ValueError("real question type is missing or unsupported")
        source_url = str(raw.get("source_url") or "").strip()
        source_locator = str(
            raw.get("source_locator") or raw.get("locator") or ""
        ).strip()
        question_number = str(raw.get("question_number") or "").strip()
        if not source_url and not source_locator and not question_number:
            raise ValueError("real question requires source_url or source locator")
        verification_status = str(raw.get("verification_status") or "").strip().lower()
        if verification_status not in {"verified", "official", "user_verified"}:
            raise ValueError("real question verification_status is not verified")
        options = raw.get("options", [])
        if question_type in {"single_choice", "multiple_choice"} and not isinstance(
            options, (list, tuple, dict)
        ):
            raise ValueError("choice question options must be a list or object")
        if question_type in {"single_choice", "multiple_choice"} and len(options) < 2:
            raise ValueError("choice question requires at least two options")
        item = cls(
            source_name=source_name,
            exam_name=exam_name,
            subject=subject,
            question_type=question_type,
            stem=stem,
            options=options,
            answer=raw.get("answer"),
            explanation=str(raw.get("explanation") or "").strip(),
            answer_source=str(raw.get("answer_source") or "not_provided").strip(),
            verification_status=verification_status,
            source_url=source_url,
            source_locator=source_locator,
            exam_year=str(raw.get("exam_year") or raw.get("year") or "").strip(),
            exam_date=str(raw.get("exam_date") or "").strip(),
            paper=str(raw.get("paper") or "").strip(),
            question_number=question_number,
            metadata=dict(raw.get("metadata") or {}),
        )
        canonical = {
            "source_name": item.source_name,
            "exam_name": item.exam_name,
            "subject": item.subject,
            "question_type": item.question_type,
            "stem": item.stem,
            "options": item.options,
            "source_url": item.source_url,
            "source_locator": item.source_locator,
            "question_number": item.question_number,
            "answer": item.answer,
            "explanation": item.explanation,
            "answer_source": item.answer_source,
            "verification_status": item.verification_status,
            "metadata": item.metadata,
        }
        return replace(item, content_hash=_content_hash(canonical))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_name": self.source_name,
            "exam_name": self.exam_name,
            "subject": self.subject,
            "question_type": self.question_type,
            "stem": self.stem,
            "options": self.options,
            "answer": self.answer,
            "explanation": self.explanation,
            "answer_source": self.answer_source,
            "verification_status": self.verification_status,
            "source_url": self.source_url,
            "source_locator": self.source_locator,
            "exam_year": self.exam_year,
            "exam_date": self.exam_date,
            "paper": self.paper,
            "question_number": self.question_number,
            "metadata": self.metadata,
            "content_hash": self.content_hash,
        }


def load_real_questions(path: str | Path) -> list[RealQuestion]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("questions", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise TypeError("question bank JSON must be a list or {questions: [...]}")
    return [RealQuestion.from_mapping(item) for item in records]


def validate_generated_question(content: Any, question_type: str | None) -> bool:
    if not isinstance(content, dict):
        return False
    if not str(content.get("question") or "").strip():
        return False
    answer = content.get("answer")
    if not _has_nonempty_value(answer):
        return False
    if not str(content.get("explanation") or "").strip():
        return False
    if question_type in {"single_choice", "multiple_choice"}:
        options = content.get("options")
        if not isinstance(options, (list, dict)) or len(options) < 2:
            return False
        if question_type == "single_choice" and isinstance(
            answer, (list, tuple, set, dict)
        ):
            return len(answer) == 1
    if question_type == "true_false":
        if isinstance(answer, bool):
            return True
        return str(answer).strip().lower() in {
            "正确",
            "错误",
            "对",
            "错",
            "true",
            "false",
        }
    return question_type != "case_analysis" or bool(
        content.get("questions") or content.get("issues")
    )


def _has_nonempty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def format_question_content(result: dict[str, Any]) -> str:
    origin = result.get("origin", "mock")
    subject = SUBJECT_LABELS.get(
        result.get("subject", ""), result.get("subject", "其他")
    )
    question_type = QUESTION_TYPE_LABELS.get(
        result.get("question_type", ""), result.get("question_type", "")
    )
    label = "真题" if origin == "real" else "模拟题"
    content = result.get("content", result)
    lines = [f"【{label}｜{subject}｜{question_type}】"]
    if origin == "real":
        lines.append(
            f"考试来源：{result.get('exam_name') or result.get('source_name') or '未提供'}"
        )
        if result.get("exam_year"):
            lines.append(f"考试年份：{result['exam_year']}")
        locator = result.get("source_locator") or result.get("question_number")
        if locator:
            lines.append(f"试卷/题号：{locator}")
    if isinstance(content, dict):
        labels = {
            "question": "题干",
            "stem": "题干",
            "options": "选项",
            "questions": "问题",
            "answer": "参考答案",
            "explanation": "解析",
            "source_note": "来源说明",
        }
        for key, value in content.items():
            if key in {"disclaimer", "origin", "subject", "question_type"}:
                continue
            lines.append(f"{labels.get(key, key)}：{value}")
    else:
        lines.append(str(content))
    if origin == "real":
        if result.get("answer_source"):
            lines.append(f"答案核验：{result['answer_source']}")
        if result.get("source_url"):
            lines.append(f"题目来源：{result['source_url']}")
        elif result.get("source_locator"):
            lines.append(f"题目来源记录：{result['source_locator']}")
    else:
        lines.append("由 AI 生成的学习练习题，非官方考试真题。")
    return "\n".join(lines)


def _required_text(
    raw: dict[str, Any], name: str, aliases: tuple[str, ...] = ()
) -> str:
    for key in (name, *aliases):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    raise ValueError(f"real question requires {name}")


def _content_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "QUESTION_TYPE_LABELS",
    "SUBJECT_LABELS",
    "QuestionRequest",
    "RealQuestion",
    "format_question_content",
    "load_real_questions",
    "normalize_origin",
    "normalize_question_type",
    "normalize_selection_mode",
    "normalize_subject",
    "subject_filter",
    "subject_matches",
    "validate_generated_question",
]
