from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

if __package__ and "." in __package__:
    from .document_extractors import DocumentSegment, ParsedDocument
else:
    from document_extractors import DocumentSegment, ParsedDocument


MAX_CANDIDATES = 100
_CASE_HEADING = re.compile(
    r"^\s*(?:(?:第\s*[一二三四五六七八九十百\d]+\s*案)|(?:案例|案件)\s*[一二三四五六七八九十百\d]*)\s*[:：、.．\s]*"
)
_QUESTION_HEADING = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百\d]+\s*[题问]|[一二三四五六七八九十百\d]+\s*[、.)．])"
)
_OPTION = re.compile(r"^\s*([A-HＡ-Ｈ])\s*[.．、:：)]\s*(.+)$")
_ANSWER = re.compile(
    r"^\s*(?:第\s*)?([一二三四五六七八九十百\d]+)\s*(?:题)?\s*[:：.．、]?\s*([A-HＡ-Ｈ对错√×]+)\s*$",
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
        _QUESTION_HEADING.match(line) for line, _ in lines
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
        return SegmentationResult(
            (
                {
                    "material_type": "case",
                    "title": document.original_filename,
                    "raw_text": document.text,
                    "locator": lines[0][1],
                    "subjects": [],
                    "structured": {
                        "case_summary": document.text[:1200],
                        "evidence_text": document.text,
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
    starts = [
        index for index, (line, _) in enumerate(lines) if _QUESTION_HEADING.match(line)
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
    answer_map = _answer_map(lines)
    candidates: list[dict[str, Any]] = []
    for ordinal, start in enumerate(starts[:max_candidates]):
        end = starts[ordinal + 1] if ordinal + 1 < len(starts) else len(lines)
        block = lines[start:end]
        number = _question_number(block[0][0])
        question_type = _question_type(block)
        options = []
        body_lines = []
        for line, _ in block:
            option = _OPTION.match(line)
            if option:
                options.append(option.group(2).strip())
            elif not _QUESTION_HEADING.match(line) and not line.startswith(
                ("答案", "参考答案")
            ):
                body_lines.append(line)
        stem = "\n".join(body_lines).strip()
        answer = answer_map.get(number)
        review_reason = ""
        if not stem:
            review_reason = "题干为空"
        elif question_type in {"single_choice", "multiple_choice"} and len(options) < 2:
            review_reason = "选择题选项不足"
        elif answer is None and any(
            line.startswith(("答案", "参考答案")) for line, _ in block
        ):
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
        if review_reason:
            candidate.update(status="needs_review", review_reason=review_reason)
        candidates.append(candidate)
    return SegmentationResult(tuple(candidates), ())


def _question_number(text: str) -> str:
    match = re.search(r"(?:第\s*)?([一二三四五六七八九十百\d]+)\s*[题问]", text)
    return match.group(1) if match else ""


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
    in_answer_section = False
    result: dict[str, str] = {}
    for line, _ in lines:
        if line.startswith(("答案", "参考答案")):
            in_answer_section = True
            continue
        if in_answer_section:
            match = _ANSWER.match(line)
            if match:
                result[match.group(1)] = match.group(2)
    return result


def _locator_range(block: list[tuple[str, str]]) -> str:
    start = block[0][1]
    end = block[-1][1]
    return start if start == end else f"{start}-{end}"


__all__ = [
    "MAX_CANDIDATES",
    "SegmentationResult",
    "segment_document",
    "segment_official_cases",
]
