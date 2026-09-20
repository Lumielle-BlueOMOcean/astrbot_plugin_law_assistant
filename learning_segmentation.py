from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

if __package__ and "." in __package__:
    from .document_extractors import DocumentSegment, ParsedDocument
else:
    from document_extractors import DocumentSegment, ParsedDocument


MAX_CANDIDATES = 100
CASE_SEGMENTATION_VERSION = "2"
_NUMBER = r"[一二三四五六七八九十百千万\d]+"
_CASE_HEADING = re.compile(
    r"^\s*(?:(?:第\s*[一二三四五六七八九十百\d]+\s*案)|(?:案例|案件)\s*[一二三四五六七八九十百\d]*)\s*[:：、.．\s]*"
)
_QUESTION_HEADING = re.compile(
    rf"^\s*(?:(?:第\s*{_NUMBER}\s*[题问])|(?:{_NUMBER}\s*[、.)．])|(?:[（(]\s*{_NUMBER}\s*[）)]))"
)
_OPTION = re.compile(r"^\s*([A-HＡ-Ｈ])\s*[.．、:：)]\s*(.+)$")
_ANSWER_SECTION_TITLE = re.compile(
    r"^\s*(?:参考答案(?:及解析|与解析)?|答案(?:及解析|与解析)?)\s*[:：]?\s*$"
)
_ANSWER = re.compile(
    rf"^\s*(?:第\s*)?({_NUMBER})\s*(?:题|问)?\s*(?:[:：.．、)]\s*|\s+)([A-HＡ-Ｈ对错√×]+)\s*$",
    re.IGNORECASE,
)
_PAREN_ANSWER = re.compile(
    rf"^\s*[（(]\s*({_NUMBER})\s*[）)]\s*[:：.．、]?\s*([A-HＡ-Ｈ对错√×]+)\s*$",
    re.IGNORECASE,
)
_INLINE_ANSWER = re.compile(
    r"^\s*(?:参考答案|答案)\s*[:：]\s*([A-HＡ-Ｈ对错√×]+)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    candidates: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...] = ()


def segment_document(
    document: ParsedDocument,
    *,
    content_kind: str = "auto",
    max_candidates: int = MAX_CANDIDATES,
) -> SegmentationResult:
    kind = str(content_kind or "auto").strip().lower()
    if kind not in {"auto", "case", "mock_question", "real_question_candidate"}:
        raise ValueError(
            "content_kind 必须是 auto、case、mock_question 或 real_question_candidate"
        )
    lines = _lines(document.segments)
    if not lines:
        return SegmentationResult((), ("没有可拆分文本",))
    question_mode = kind in {"mock_question", "real_question_candidate"} or any(
        _is_question_start(line) for line, _ in lines
    )
    if question_mode:
        return _segment_questions(
            lines, document, kind if kind != "auto" else "mock_question", max_candidates
        )
    return _segment_cases(lines, document, max_candidates)


def segment_official_cases(
    document: ParsedDocument, *, max_candidates: int = MAX_CANDIDATES
) -> SegmentationResult:
    return _segment_cases(
        _lines(document.segments), document, max_candidates, official=True
    )


def _lines(segments: tuple[DocumentSegment, ...]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for segment in segments:
        lines = segment.text.splitlines() or [segment.text]
        for line_index, line in enumerate(lines, 1):
            if line.strip():
                locator = (
                    segment.locator
                    if len(lines) == 1
                    else f"{segment.locator}/第{line_index}行"
                )
                result.append((line.strip(), locator))
    return result


def _segment_cases(
    lines: list[tuple[str, str]],
    document: ParsedDocument,
    max_candidates: int,
    *,
    official: bool = False,
) -> SegmentationResult:
    headings = [
        index for index, (line, _) in enumerate(lines) if _CASE_HEADING.match(line)
    ]
    if not headings:
        named_headings = [
            index for index, (line, _) in enumerate(lines) if _named_case_heading(line)
        ]
        if len(named_headings) >= 2:
            headings = named_headings
    if not headings:
        if not _is_confirmed_single_case(lines, document):
            return SegmentationResult(
                (
                    {
                        "status": "needs_review",
                        "review_reason": "无法确认官方文章是否对应单一独立案件",
                        "locator": lines[0][1],
                        "raw_text": document.text,
                        "structured": {"evidence_text": document.text},
                        "trusted_official": official,
                    },
                )
            )
        return SegmentationResult(
            (
                {
                    "material_type": "case",
                    "title": (lines[0][0] or document.original_filename)[:200],
                    "raw_text": document.text,
                    "locator": lines[0][1],
                    "subjects": [],
                    "structured": {
                        "case_summary": document.text[:1200],
                        "evidence_text": document.text,
                        "case_segmentation_version": CASE_SEGMENTATION_VERSION,
                    },
                    "trusted_official": official,
                },
            )
        )
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    for ordinal, start in enumerate(headings):
        end = headings[ordinal + 1] if ordinal + 1 < len(headings) else len(lines)
        block = lines[start:end]
        raw_text = "\n".join(line for line, _ in block).strip()
        if len(raw_text) < 20:
            candidates.append(
                {
                    "status": "needs_review",
                    "review_reason": "案例边界内容过短",
                    "locator": block[0][1],
                }
            )
            continue
        heading = block[0][0]
        candidates.append(
            {
                "material_type": "case",
                "title": heading[:200],
                "raw_text": raw_text,
                "locator": _locator_range(block),
                "subjects": [],
                "structured": {
                    "case_summary": raw_text[:1200],
                    "evidence_text": raw_text,
                    "original_title": heading,
                    "case_segmentation_version": CASE_SEGMENTATION_VERSION,
                },
                "trusted_official": official,
            }
        )
    if len(candidates) > max_candidates:
        warnings.append(f"候选条目超过 {max_candidates} 条，后续条目待复核")
        candidates = candidates[:max_candidates]
    return SegmentationResult(tuple(candidates), tuple(warnings))


def _segment_questions(
    lines: list[tuple[str, str]],
    document: ParsedDocument,
    material_type: str,
    max_candidates: int,
) -> SegmentationResult:
    answer_section_start = _answer_section_start(lines)
    question_lines = (
        lines[:answer_section_start] if answer_section_start is not None else lines
    )
    starts = [
        index
        for index, (line, _) in enumerate(question_lines)
        if _is_question_start(line)
    ]
    if not starts:
        return SegmentationResult(
            (
                {
                    "status": "needs_review",
                    "review_reason": "未找到明确题号边界",
                    "locator": lines[0][1],
                    "raw_text": document.text,
                },
            )
        )
    answer_map, answer_locators = _parse_answer_section(lines, answer_section_start)
    candidates: list[dict[str, Any]] = []
    for ordinal, start in enumerate(starts[:max_candidates]):
        end = starts[ordinal + 1] if ordinal + 1 < len(starts) else len(question_lines)
        block = question_lines[start:end]
        number = _question_number(block[0][0])
        question_type = _question_type(block)
        options = []
        body_lines = []
        inline_answer = None
        inline_answer_locator = ""
        inline_answer_section = False
        for line, locator in block:
            option = _OPTION.match(line)
            if option:
                options.append(option.group(2).strip())
            elif _is_question_start(line):
                continue
            elif _ANSWER_SECTION_TITLE.match(line):
                inline_answer_section = True
                continue
            elif inline_answer_section:
                parsed_answer = _answer_row(line)
                if parsed_answer is not None:
                    parsed_number, parsed_value = parsed_answer
                    if parsed_number == number:
                        inline_answer = parsed_value
                        inline_answer_locator = locator
                    continue
                inline_answer_section = False
            elif (inline := _INLINE_ANSWER.match(line)) is not None:
                inline_answer = _normalize_answer(inline.group(1))
                inline_answer_locator = locator
            elif not _ANSWER_SECTION_TITLE.match(line):
                body_lines.append(line)
        stem = "\n".join(body_lines).strip()
        answer = answer_map.get(number) or inline_answer
        answer_locator = answer_locators.get(number) or inline_answer_locator
        review_reason = ""
        if not stem:
            review_reason = "题干为空"
        elif question_type in {"single_choice", "multiple_choice"} and len(options) < 2:
            review_reason = "选择题选项不足"
        elif (
            answer_section_start is not None or inline_answer_section
        ) and answer is None:
            review_reason = "答案区存在但无法与题号匹配"
        candidate: dict[str, Any] = {
            "material_type": material_type,
            "title": block[0][0][:200],
            "raw_text": "\n".join(line for line, _ in block).strip(),
            "locator": _locator_range(block),
            "subjects": [],
            "structured": {
                "question_type": question_type,
                "stem": stem,
                "options": options,
                "answer": answer,
                "answer_source": "source_text"
                if answer is not None
                else "not_provided",
            },
        }
        if answer_locator:
            candidate["structured"]["answer_locator"] = answer_locator
        if review_reason:
            candidate.update(status="needs_review", review_reason=review_reason)
        candidates.append(candidate)
    return SegmentationResult(tuple(candidates), ())


def _question_number(text: str) -> str:
    patterns = (
        rf"^\s*第\s*({_NUMBER})\s*[题问]",
        rf"^\s*[（(]\s*({_NUMBER})\s*[）)]",
        rf"^\s*({_NUMBER})\s*[、.)．]",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _number_key(match.group(1))
    return ""


def _question_type(block: list[tuple[str, str]]) -> str:
    text = " ".join(line for line, _ in block[:2])
    if "多选" in text or "多项选择" in text:
        return "multiple_choice"
    if "判断" in text:
        return "true_false"
    if "简答" in text:
        return "short_answer"
    if "案例分析" in text:
        return "case_analysis"
    return (
        "single_choice"
        if any(_OPTION.match(line) for line, _ in block)
        else "short_answer"
    )


def _answer_map(lines: list[tuple[str, str]]) -> dict[str, str]:
    result, _ = _parse_answer_section(lines, _answer_section_start(lines))
    return result


def _answer_section_start(lines: list[tuple[str, str]]) -> int | None:
    question_seen = False
    for index, (line, _) in enumerate(lines):
        if _is_question_start(line):
            question_seen = True
        if not question_seen or not _ANSWER_SECTION_TITLE.match(line):
            continue
        rows = lines[index + 1 :]
        if any(_is_question_start(row) for row, _ in rows):
            continue
        if any(_answer_row(row) is not None for row, _ in rows):
            return index
    return None


def _parse_answer_section(
    lines: list[tuple[str, str]], start: int | None
) -> tuple[dict[str, str], dict[str, str]]:
    if start is None:
        return {}, {}
    answers: dict[str, str] = {}
    locators: dict[str, str] = {}
    for line, locator in lines[start + 1 :]:
        parsed = _answer_row(line)
        if parsed is None:
            continue
        number, answer = parsed
        answers[number] = answer
        locators[number] = locator
    return answers, locators


def _answer_row(line: str) -> tuple[str, str] | None:
    match = _ANSWER.match(line) or _PAREN_ANSWER.match(line)
    if match is None:
        return None
    return _number_key(match.group(1)), _normalize_answer(match.group(2))


def _is_question_start(line: str) -> bool:
    return _QUESTION_HEADING.match(line) is not None and _answer_row(line) is None


def _normalize_answer(value: str) -> str:
    return re.sub(r"[\s,，、/]+", "", value).translate(
        str.maketrans("ＡＢＣＤＥＦＧＨ", "ABCDEFGH")
    )


def _number_key(value: str) -> str:
    if value.isdigit():
        return str(int(value))
    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return str(digits[value])
    if value == "十":
        return "10"
    if "十" in value:
        left, _, right = value.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return str(tens * 10 + ones)
    return value


def _named_case_heading(line: str) -> bool:
    value = line.strip()
    if not value or len(value) > 120 or any(mark in value for mark in "，。；"):
        return False
    return value.endswith(("案", "纠纷", "争议", "判决", "裁定"))


def _is_confirmed_single_case(
    lines: list[tuple[str, str]], document: ParsedDocument
) -> bool:
    title = (
        next((line.strip() for line, _ in lines if line.strip()), "")
        or document.original_filename
    ).lower()
    text = document.text
    case_number_count = len(re.findall(r"[（(]?20\d{2}[）)]?[^\n]{0,24}?号", text))
    collection_clues = (
        case_number_count > 1
        or bool(re.search(r"(?:多个|三|四|五|十|[3-9]\d*)个(?:典型)?案例", text))
        or text.count("案例一") > 1
        or text.count("案例二") > 1
    )
    if collection_clues:
        return False
    has_title_case_marker = bool(
        re.search(r"(?:案件|案例|纠纷|争议|判决|裁定|诉讼|侵权|合同)", title)
    )
    has_body_case_structure = bool(
        re.search(r"(?:基本案情|裁判要旨|裁判结果|判决结果|检察机关|指控意见)", text)
    )
    if len(text.strip()) < 20:
        return False
    if "目录" in text or re.search(r"(?:新闻导语|本期发布|公告|通知)[:：]?", text):
        return False
    return bool(
        case_number_count == 1 or (has_title_case_marker and has_body_case_structure)
    )


def _locator_range(block: list[tuple[str, str]]) -> str:
    start = block[0][1]
    end = block[-1][1]
    return start if start == end else f"{start}-{end}"


__all__ = [
    "CASE_SEGMENTATION_VERSION",
    "MAX_CANDIDATES",
    "SegmentationResult",
    "segment_document",
    "segment_official_cases",
]
