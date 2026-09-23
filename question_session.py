from __future__ import annotations

import hashlib
import json
from typing import Any

if __package__ and "." in __package__:
    from .content import QUESTION_TYPE_LABELS, SUBJECT_LABELS
else:
    from content import QUESTION_TYPE_LABELS, SUBJECT_LABELS


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split text at readable boundaries without dropping or rewriting characters."""
    value = str(text or "")
    if not value:
        return []
    limit = max(1, int(max_chars))
    chunks: list[str] = []
    start = 0
    preferred = "\n。！？；;.!?，,、：: "
    while start < len(value):
        end = min(start + limit, len(value))
        if end < len(value):
            boundary = max(value.rfind(char, start, end) for char in preferred)
            if boundary >= start + max(1, limit // 2):
                end = boundary + 1
        chunks.append(value[start:end])
        start = end
    return chunks


def _ordered_block_texts(blocks: Any) -> list[str]:
    if isinstance(blocks, str):
        return [blocks] if blocks else []
    if not isinstance(blocks, (list, tuple)):
        return []
    ordered = sorted(
        (block for block in blocks if isinstance(block, dict)),
        key=lambda block: (int(block.get("order", 0) or 0), str(block.get("id", ""))),
    )
    return [str(block.get("text") or "") for block in ordered if block.get("text")]


def _paginate_blocks(blocks: Any, max_chars: int) -> list[str]:
    """Keep structured blocks intact when possible; split only oversized blocks."""
    limit = max(1, int(max_chars))
    pages: list[str] = []
    current = ""
    for block in _ordered_block_texts(blocks):
        if len(block) > limit:
            if current:
                pages.append(current)
                current = ""
            pages.extend(chunk_text(block, limit))
            continue
        candidate = f"{current}\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                pages.append(current)
            current = block
    if current:
        pages.append(current)
    return pages


def _block_text(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, (list, tuple)):
        return ""
    ordered = sorted(
        (block for block in blocks if isinstance(block, dict)),
        key=lambda block: (int(block.get("order", 0) or 0), str(block.get("id", ""))),
    )
    return "\n".join(
        str(block.get("text") or "") for block in ordered if block.get("text")
    )


def _answer_blocks(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("blocks", value.get("text", value.get("answer", "")))
    if isinstance(value, (list, tuple)):
        return [
            text
            for text in (
                _block_text([item]) if isinstance(item, dict) else str(item or "")
                for item in value
            )
            if text
        ]
    text = str(value or "").strip()
    return [text] if text else []


def _prompt_blocks(blocks: Any, max_chars: int) -> list[str]:
    return _paginate_blocks(blocks, max_chars)


def build_question_session_snapshot(
    result: dict[str, Any], *, max_chars: int = 1600
) -> dict[str, Any]:
    """Create an immutable, answer-separated snapshot for an interactive session."""
    origin = str(result.get("origin") or "mock")
    subject = str(result.get("subject") or "other")
    question_type = str(result.get("question_type") or "")
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    question = (
        result.get("question") if isinstance(result.get("question"), dict) else {}
    )
    structured = (
        result.get("structured") if isinstance(result.get("structured"), dict) else {}
    )
    identity = str(
        question.get("question_identity")
        or item.get("identity")
        or result.get("question_identity")
        or ("verified_real_question" if origin == "real" else "mock_question")
    )
    is_candidate = identity == "real_question_candidate"
    identity_label = (
        "来源资料候选题 · 待人工核验"
        if is_candidate
        else (
            "真题"
            if origin == "real" or identity == "verified_real_question"
            else "模拟题"
        )
    )
    if origin == "real":
        content = (
            result.get("content") if isinstance(result.get("content"), dict) else {}
        )
        stem = str(content.get("question") or content.get("stem") or "")
        options = content.get("options") or []
        answer_blocks = _answer_blocks(content.get("answer"))
        explanation_blocks = _answer_blocks(content.get("explanation"))
        materials: list[dict[str, Any]] = []
        prompts = [
            {
                "id": str(result.get("question_id") or "question"),
                "source_number": "",
                "stem_pages": chunk_text(stem, max_chars),
                "options": [str(option) for option in options],
                "answer_blocks": answer_blocks,
                "explanation_blocks": explanation_blocks,
            }
        ]
        requirements: list[str] = []
        exam = {
            "source_name": str(result.get("source_name") or ""),
            "exam_name": str(result.get("exam_name") or ""),
            "exam_year": str(result.get("exam_year") or ""),
            "paper": str(result.get("paper") or ""),
            "question_number": str(result.get("question_number") or ""),
            "source_url": str(result.get("source_url") or ""),
            "answer_source": str(result.get("answer_source") or ""),
        }
    else:
        if not question:
            question = (
                result.get("content") if isinstance(result.get("content"), dict) else {}
            )
        stem_blocks = result.get("stem_blocks")
        if not stem_blocks:
            stem_blocks = question.get("stem") or question.get("question") or ""
        materials = []
        for material in result.get("shared_materials", []) or []:
            if not isinstance(material, dict):
                continue
            materials.append(
                {
                    "id": str(material.get("id") or ""),
                    "title": str(material.get("title") or ""),
                    "pages": _paginate_blocks(material.get("blocks", []), max_chars),
                }
            )
        subquestions = result.get("subquestions", []) or []
        prompts = []
        if isinstance(subquestions, list) and subquestions:
            for index, subquestion in enumerate(subquestions):
                if not isinstance(subquestion, dict):
                    continue
                prompts.append(
                    {
                        "id": str(subquestion.get("id") or index + 1),
                        "source_number": str(
                            subquestion.get("source_number") or index + 1
                        ),
                        "stem_pages": _prompt_blocks(
                            subquestion.get("stem_blocks", []), max_chars
                        ),
                        "options": [],
                        "answer_blocks": _answer_blocks(subquestion.get("answer")),
                        "explanation_blocks": _prompt_blocks(
                            subquestion.get("explanation_blocks", []), max_chars
                        ),
                    }
                )
        else:
            prompts.append(
                {
                    "id": str(item.get("id") or "question"),
                    "source_number": "",
                    "stem_pages": _prompt_blocks(stem_blocks, max_chars),
                    "options": [
                        str(option) for option in question.get("options", []) or []
                    ],
                    "answer_blocks": _answer_blocks(question.get("answer")),
                    "explanation_blocks": _answer_blocks(question.get("explanation")),
                }
            )
        payload = (
            structured.get("payload")
            if isinstance(structured.get("payload"), dict)
            else {}
        )
        requirements = [
            str(value.get("text") or "")
            for value in (
                structured.get("answer_requirements")
                or payload.get("answer_requirements")
                or []
            )
            if isinstance(value, dict) and str(value.get("text") or "").strip()
        ]
        exam = {
            key: str(question.get(key) or "")
            for key in (
                "source_name",
                "exam_name",
                "exam_year",
                "paper",
                "question_number",
                "source_url",
                "answer_source",
            )
        }
        origin = (
            "candidate"
            if is_candidate
            else str(question.get("question_identity") or identity)
        )
        subject = str(
            (payload.get("subject") or (item.get("subjects") or [subject])[0])
            if isinstance(item.get("subjects") or [], (list, tuple))
            else subject
        )
        question_type = str(
            payload.get("question_type")
            or question.get("question_type")
            or question_type
        )

    snapshot = {
        "identity": identity,
        "identity_label": identity_label,
        "subject": subject,
        "subject_label": SUBJECT_LABELS.get(subject, subject or "其他"),
        "question_type": question_type,
        "question_type_label": QUESTION_TYPE_LABELS.get(question_type, question_type),
        "materials": materials,
        "prompts": prompts,
        "answer_requirements": requirements,
        "exam": exam,
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    snapshot["snapshot_hash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return snapshot


def format_session_prompt(
    snapshot: dict[str, Any],
    *,
    material_index: int | None = None,
    material_page: int = 0,
    prompt_index: int = 0,
    prompt_page: int = 0,
) -> str:
    lines = [
        f"【{snapshot.get('identity_label', '题目')}｜{snapshot.get('subject_label', '其他')}｜{snapshot.get('question_type_label', '')}】"
    ]
    if snapshot.get("identity") == "verified_real_question":
        exam = snapshot.get("exam", {})
        if exam.get("source_name"):
            lines.append(f"题库来源：{exam['source_name']}")
        if exam.get("exam_name"):
            lines.append(f"考试：{exam['exam_name']}")
        if exam.get("exam_year"):
            lines.append(f"年份：{exam['exam_year']}")
        if exam.get("source_url"):
            lines.append(f"来源链接：{exam['source_url']}")
    materials = snapshot.get("materials", [])
    if material_index is not None and 0 <= material_index < len(materials):
        material = materials[material_index]
        pages = material.get("pages", [])
        if pages:
            lines.append(
                f"材料 {material_index + 1}/{len(materials)}（第 {material_page + 1}/{len(pages)} 页）"
            )
            if material.get("title"):
                lines.append(str(material["title"]))
            lines.append(pages[material_page])
        return "\n".join(lines)
    prompt_list = snapshot.get("prompts", [])
    if not prompt_list:
        return "\n".join(lines + ["当前题目没有可展示的题干。"])
    prompt_index = min(max(0, prompt_index), len(prompt_list) - 1)
    prompt = prompt_list[prompt_index]
    if len(prompt_list) > 1:
        lines.append(f"小问 {prompt_index + 1}/{len(prompt_list)}")
    pages = prompt.get("stem_pages", [])
    if pages:
        if len(pages) > 1:
            lines.append(f"题干续页 {prompt_page + 1}/{len(pages)}")
        lines.append("题干：" + pages[prompt_page])
    for option in prompt.get("options", []):
        lines.append(str(option))
    if snapshot.get("answer_requirements"):
        lines.append("答题要求：" + "；".join(snapshot["answer_requirements"]))
    lines.append("答案与解析可分别使用 /law answer 和 /law explanation 查看。")
    return "\n".join(lines)


def format_session_status(session: dict[str, Any]) -> str:
    """Summarize progress without retransmitting source text or hidden content."""
    snapshot = session["snapshot"]
    material_count = len(snapshot.get("materials", []))
    material_index = min(session.get("current_material_index", 0), material_count)
    prompt_count = len(snapshot.get("prompts", []))
    prompt_index = min(session.get("current_prompt_index", 0), max(0, prompt_count - 1))
    lines = [
        (
            f"当前题目：{snapshot.get('identity_label', '题目')}｜"
            f"{snapshot.get('subject_label', '其他')}｜{snapshot.get('question_type_label', '')}"
        )
    ]
    exam = snapshot.get("exam", {})
    if exam.get("exam_name") or exam.get("exam_year"):
        lines.append(
            "考试："
            + " ".join(
                value
                for value in (
                    str(exam.get("exam_year") or ""),
                    str(exam.get("exam_name") or ""),
                )
                if value
            )
        )
    lines.append(f"材料进度：{material_index}/{material_count}")
    if material_index < material_count:
        pages = snapshot["materials"][material_index].get("pages", [])
        lines.append(
            f"当前阅读：材料 {material_index + 1}/{material_count}，"
            f"第 {session.get('current_material_page', 0) + 1}/{max(1, len(pages))} 页"
        )
    if prompt_count:
        lines.append(f"小问：{prompt_index + 1}/{prompt_count}")
        pages = snapshot["prompts"][prompt_index].get("stem_pages", [])
        lines.append(
            f"题干页：{session.get('current_prompt_page', 0) + 1}/{max(1, len(pages))}"
        )
    lines.append(f"答案：{'已揭晓' if session.get('answer_revealed') else '未揭晓'}")
    lines.append(
        f"解析：{'已揭晓' if session.get('explanation_revealed') else '未揭晓'}"
    )
    return "\n".join(lines)


def format_session_answer(snapshot: dict[str, Any], *, prompt_index: int = 0) -> str:
    prompts = snapshot.get("prompts", [])
    if not prompts:
        return "当前题目没有可用的参考答案。"
    prompt = prompts[min(max(0, prompt_index), len(prompts) - 1)]
    blocks = prompt.get("answer_blocks", [])
    if not blocks:
        return "原始资料未提供可核验参考答案；不会使用模型补写答案。"
    identity = snapshot.get("identity")
    if identity == "real_question_candidate":
        label = "来源资料参考答案 · 待人工核验"
    elif identity == "verified_real_question":
        answer_source = str(snapshot.get("exam", {}).get("answer_source") or "")
        source_label = {
            "official": "官方答案来源标注",
            "third_party": "第三方参考答案来源标注",
        }.get(answer_source, "题库所存答案")
        label = f"真题参考答案 · {source_label}"
    else:
        label = "模拟题参考答案"
    return f"【{label}】\n" + "\n".join(str(block) for block in blocks)


def format_session_explanation(
    snapshot: dict[str, Any], *, prompt_index: int = 0
) -> str:
    prompts = snapshot.get("prompts", [])
    if not prompts:
        return "当前题目没有可用解析。"
    blocks = prompts[min(max(0, prompt_index), len(prompts) - 1)].get(
        "explanation_blocks", []
    )
    if not blocks:
        return "该题没有来源提供的独立解析；不会让模型猜测补充。"
    identity = snapshot.get("identity")
    label = (
        "来源资料解析 · 待人工核验"
        if identity == "real_question_candidate"
        else ("模拟题解析" if identity != "verified_real_question" else "题库所存解析")
    )
    return f"【{label}】\n" + "\n".join(str(block) for block in blocks)
