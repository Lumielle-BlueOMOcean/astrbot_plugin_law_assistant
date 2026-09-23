from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
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


def _requirements(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    ordered = sorted(
        (value for value in values if isinstance(value, dict)),
        key=lambda value: (int(value.get("order", 0) or 0), str(value.get("id", ""))),
    )
    return [
        str(value.get("text") or "")
        for value in ordered
        if str(value.get("text") or "").strip()
    ]


def _header(snapshot: dict[str, Any]) -> str:
    return (
        f"【{snapshot.get('identity_label', '题目')}｜"
        f"{snapshot.get('subject_label', '其他')}｜"
        f"{snapshot.get('question_type_label', '')}】"
    )


def _answer_label(snapshot: dict[str, Any]) -> str:
    identity = snapshot.get("identity")
    if identity == "real_question_candidate":
        return "来源资料参考答案 · 待人工核验"
    if identity == "verified_real_question":
        answer_source = str(snapshot.get("exam", {}).get("answer_source") or "")
        source_label = {
            "official": "官方答案来源标注",
            "third_party": "第三方参考答案来源标注",
        }.get(answer_source, "题库所存答案")
        return f"真题参考答案 · {source_label}"
    return "模拟题参考答案"


def _explanation_label(snapshot: dict[str, Any]) -> str:
    identity = snapshot.get("identity")
    if identity == "real_question_candidate":
        return "来源资料解析 · 待人工核验"
    if identity == "verified_real_question":
        return "题库所存解析"
    return "模拟题解析"


def _normalized_text(text: str) -> str:
    return "".join(str(text or "").split())


def _compose_message(prefix: str, body: str, suffix: str) -> str:
    return "\n".join(part for part in (prefix, body, suffix) if part)


def _paginate_message_blocks(
    blocks: Any,
    max_chars: int,
    *,
    prefix_for_page: Callable[[int, int], str],
    suffix_for_page: Callable[[int, int], str] | None = None,
) -> list[str]:
    """Paginate semantic blocks against the complete final message budget."""
    limit = max(1, int(max_chars))
    source = [str(block) for block in blocks if str(block or "")]
    suffix_for_page = suffix_for_page or (lambda _page, _total: "")
    total = 1

    for _ in range(24):
        messages: list[str] = []
        current = ""
        pending = [(block, index > 0) for index, block in enumerate(source)]
        page = 1

        while pending:
            block, separate = pending.pop(0)
            prefix = prefix_for_page(page, total)
            suffix = suffix_for_page(page, total)
            candidate_body = current + ("\n" if current and separate else "") + block
            if len(_compose_message(prefix, candidate_body, suffix)) <= limit:
                current = candidate_body
                continue

            if current:
                messages.append(_compose_message(prefix, current, suffix))
                page += 1
                current = ""
                pending.insert(0, (block, separate))
                continue

            empty_message_length = len(_compose_message(prefix, "", suffix))
            available = limit - empty_message_length - 1
            if available < 1:
                raise ValueError(
                    "question_message_max_chars is too small for fixed message text"
                )
            chunks = chunk_text(block, available)
            if not chunks:
                continue
            first, *rest = chunks
            message = _compose_message(prefix, first, suffix)
            if len(message) > limit:
                raise ValueError(
                    "question_message_max_chars is too small for fixed message text"
                )
            messages.append(message)
            page += 1
            pending[:0] = [(chunk, False) for chunk in rest]

        if current:
            page_prefix = prefix_for_page(page, total)
            messages.append(
                _compose_message(page_prefix, current, suffix_for_page(page, total))
            )
        if not messages:
            messages.append(
                _compose_message(
                    prefix_for_page(1, total), "", suffix_for_page(1, total)
                )
            )
        if len(messages) == total:
            return messages
        total = len(messages)

    raise ValueError("could not stabilize question message pagination")


def _material_pages(
    snapshot: dict[str, Any],
    index: int,
    material: dict[str, Any],
    max_chars: int,
) -> list[str]:
    materials = snapshot.get("materials", [])
    count = len(materials)
    body: list[str] = []
    if material.get("title"):
        body.append(f"材料标题：{material['title']}")
    raw_blocks = material.get("blocks", [])
    if isinstance(raw_blocks, (list, tuple)) and all(
        isinstance(block, str) for block in raw_blocks
    ):
        body.extend(str(block) for block in raw_blocks if block)
    else:
        body.extend(_ordered_block_texts(raw_blocks))
    if not body:
        body.extend(str(page) for page in material.get("pages", []) if page)

    def prefix(page: int, total: int) -> str:
        return f"{_header(snapshot)}\n材料 {index + 1}/{count}（第 {page}/{total} 页）"

    return _paginate_message_blocks(body, max_chars, prefix_for_page=prefix)


def _prompt_body_blocks(
    snapshot: dict[str, Any], index: int, prompt: dict[str, Any]
) -> list[str]:
    prompts = snapshot.get("prompts", [])
    shared_stem = snapshot.get("shared_stem_blocks", [])
    blocks: list[str] = []
    if index == 0:
        exam = snapshot.get("exam", {})
        if snapshot.get("identity") == "verified_real_question":
            for key, label in (
                ("source_name", "题库来源"),
                ("exam_name", "考试"),
                ("exam_year", "年份"),
                ("source_url", "来源链接"),
            ):
                if exam.get(key):
                    blocks.append(f"{label}：{exam[key]}")

        if shared_stem and len(prompts) > 1:
            blocks.append(f"公共题干：{shared_stem[0]}")
            blocks.extend(shared_stem[1:])

    prompt_stems = prompt.get("source_stem_blocks", [])
    if shared_stem and len(prompts) > 1:
        shared_text = _normalized_text("".join(shared_stem))
        if _normalized_text("".join(prompt_stems)) == shared_text:
            prompt_stems = []
        else:
            shared_blocks = {_normalized_text(text) for text in shared_stem}
            prompt_stems = [
                text
                for text in prompt_stems
                if _normalized_text(text) not in shared_blocks
                and _normalized_text(text) != shared_text
            ]
    if prompt_stems:
        blocks.append(f"题干：{prompt_stems[0]}")
        blocks.extend(prompt_stems[1:])
    else:
        blocks.append("题干：")
    options = prompt.get("source_options", [])
    if options:
        blocks.append(f"选项：{options[0]}")
        blocks.extend(str(option) for option in options[1:])

    requirements: list[str] = []
    if index == 0:
        requirements.extend(snapshot.get("answer_requirements", []))
    requirements.extend(prompt.get("source_answer_requirements", []))
    if requirements:
        blocks.append(f"答题要求：{requirements[0]}")
        blocks.extend(requirements[1:])
    return blocks


def _render_snapshot(
    snapshot: dict[str, Any],
    *,
    max_chars: int,
    shared_stem_blocks: list[str] | None = None,
) -> dict[str, Any]:
    limit = max(300, min(4000, int(max_chars)))
    snapshot["message_max_chars"] = limit
    snapshot["render_version"] = 2
    if shared_stem_blocks is not None:
        snapshot["shared_stem_blocks"] = shared_stem_blocks

    materials = snapshot.get("materials", [])
    rendered_materials = []
    for index, material in enumerate(materials):
        rendered_materials.append(
            {
                "id": str(material.get("id") or ""),
                "pages": _material_pages(snapshot, index, material, limit),
            }
        )
    snapshot["materials"] = rendered_materials

    prompts = snapshot.get("prompts", [])
    rendered_prompts = []
    for index, prompt in enumerate(prompts):
        body = _prompt_body_blocks(snapshot, index, prompt)
        prompt_count = len(prompts)

        def prompt_prefix(
            page: int,
            total: int,
            *,
            _index: int = index,
            _count: int = prompt_count,
        ) -> str:
            progress = f"小问 {_index + 1}/{_count}｜" if _count > 1 else ""
            return f"{_header(snapshot)}\n{progress}题干续页 {page}/{total}"

        stem_pages = _paginate_message_blocks(
            body,
            limit,
            prefix_for_page=prompt_prefix,
            suffix_for_page=lambda _page, _total: (
                "答案与解析可分别使用 /law answer 和 /law explanation 查看。"
            ),
        )

        answer_blocks = list(prompt.get("source_answer_blocks", []))
        if not answer_blocks:
            answer_blocks = ["原始资料未提供可核验参考答案；不会使用模型补写答案。"]
        answer_label = str(prompt.get("answer_label") or "参考答案")

        def answer_prefix(
            page: int,
            total: int,
            *,
            label: str = answer_label,
            _index: int = index,
            _count: int = prompt_count,
        ) -> str:
            progress = f"小问 {_index + 1}/{_count}｜" if _count > 1 else ""
            return f"{_header(snapshot)}\n{label}｜{progress}第 {page}/{total} 页"

        answer_pages = _paginate_message_blocks(
            answer_blocks,
            limit,
            prefix_for_page=answer_prefix,
            suffix_for_page=lambda page, total: (
                "答案未完，继续使用 /law next-answer。" if page < total else ""
            ),
        )

        explanation_blocks = list(prompt.get("source_explanation_blocks", []))
        if not explanation_blocks:
            explanation_blocks = ["该题没有来源提供的独立解析；不会让模型猜测补充。"]
        explanation_label = str(prompt.get("explanation_label") or "解析")

        def explanation_prefix(
            page: int,
            total: int,
            *,
            label: str = explanation_label,
            _index: int = index,
            _count: int = prompt_count,
        ) -> str:
            progress = f"小问 {_index + 1}/{_count}｜" if _count > 1 else ""
            return f"{_header(snapshot)}\n{label}｜{progress}第 {page}/{total} 页"

        explanation_pages = _paginate_message_blocks(
            explanation_blocks,
            limit,
            prefix_for_page=explanation_prefix,
            suffix_for_page=lambda page, total: (
                "解析未完，继续使用 /law next-explanation。" if page < total else ""
            ),
        )
        rendered_prompts.append(
            {
                "id": str(prompt.get("id") or index + 1),
                "source_number": str(prompt.get("source_number") or index + 1),
                "stem_pages": stem_pages,
                "answer_pages": answer_pages,
                "explanation_pages": explanation_pages,
            }
        )
    snapshot["prompts"] = rendered_prompts
    snapshot.pop("shared_stem_blocks", None)
    return snapshot


def prepare_question_session_snapshot(
    snapshot: dict[str, Any], *, max_chars: int | None = None
) -> dict[str, Any]:
    """Return a display-ready view, upgrading older persisted snapshots in memory."""
    if snapshot.get("render_version") == 2:
        return snapshot
    result = deepcopy(snapshot)
    result.pop("snapshot_hash", None)
    materials = []
    for material in result.get("materials", []):
        if not isinstance(material, dict):
            continue
        materials.append(
            {
                "id": material.get("id"),
                "blocks": list(material.get("pages", [])),
            }
        )
    prompts = []
    for prompt in result.get("prompts", []):
        if not isinstance(prompt, dict):
            continue
        prompts.append(
            {
                "id": prompt.get("id"),
                "source_number": prompt.get("source_number"),
                "stem_blocks": list(prompt.get("stem_pages", [])),
                "options": list(prompt.get("options", [])),
                "answer_blocks": list(prompt.get("answer_blocks", [])),
                "explanation_blocks": list(prompt.get("explanation_blocks", [])),
                "answer_requirements": list(prompt.get("answer_requirements", [])),
            }
        )
    result["materials"] = materials
    result["prompts"] = prompts
    return _render_snapshot(
        result,
        max_chars=max_chars or result.get("message_max_chars", 1600),
    )


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
    shared_stem_blocks: list[str] = []
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
                "stem_blocks": [stem] if stem else [],
                "options": [str(option) for option in options],
                "answer_blocks": answer_blocks,
                "explanation_blocks": explanation_blocks,
                "answer_requirements": [],
                "answer_label": None,
                "explanation_label": None,
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
                    "blocks": _ordered_block_texts(material.get("blocks", [])),
                }
            )
        shared_stem_blocks = _ordered_block_texts(stem_blocks)
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
                        "stem_blocks": _ordered_block_texts(
                            subquestion.get("stem_blocks", [])
                        ),
                        "options": [],
                        "answer_blocks": _answer_blocks(subquestion.get("answer")),
                        "explanation_blocks": _answer_blocks(
                            subquestion.get("explanation_blocks", [])
                        ),
                        "answer_requirements": _requirements(
                            subquestion.get("answer_requirements", [])
                        ),
                    }
                )
        else:
            prompts.append(
                {
                    "id": str(item.get("id") or "question"),
                    "source_number": "",
                    "stem_blocks": shared_stem_blocks,
                    "options": [
                        str(option) for option in question.get("options", []) or []
                    ],
                    "answer_blocks": _answer_blocks(question.get("answer")),
                    "explanation_blocks": _answer_blocks(question.get("explanation")),
                    "answer_requirements": [],
                }
            )
        payload = (
            structured.get("payload")
            if isinstance(structured.get("payload"), dict)
            else {}
        )
        requirements = _requirements(
            structured.get("answer_requirements")
            or payload.get("answer_requirements")
            or []
        )
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
    for index, prompt in enumerate(snapshot["prompts"]):
        prompt["source_stem_blocks"] = prompt.pop("stem_blocks", [])
        prompt["source_options"] = prompt.pop("options", [])
        prompt["source_answer_blocks"] = prompt.pop("answer_blocks", [])
        prompt["source_explanation_blocks"] = prompt.pop("explanation_blocks", [])
        prompt["source_answer_requirements"] = prompt.pop("answer_requirements", [])
        prompt["answer_label"] = _answer_label(snapshot)
        prompt["explanation_label"] = _explanation_label(snapshot)
    snapshot = _render_snapshot(
        snapshot,
        max_chars=max_chars,
        shared_stem_blocks=shared_stem_blocks,
    )
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
    snapshot = prepare_question_session_snapshot(snapshot)
    materials = snapshot.get("materials", [])
    if material_index is not None and 0 <= material_index < len(materials):
        material = materials[material_index]
        pages = material.get("pages", [])
        if pages:
            return pages[min(max(0, material_page), len(pages) - 1)]
        return _compose_message(_header(snapshot), "材料暂无可展示内容。", "")
    prompt_list = snapshot.get("prompts", [])
    if not prompt_list:
        return _compose_message(_header(snapshot), "当前题目没有可展示的题干。", "")
    prompt_index = min(max(0, prompt_index), len(prompt_list) - 1)
    prompt = prompt_list[prompt_index]
    pages = prompt.get("stem_pages", [])
    if not pages:
        return _compose_message(_header(snapshot), "当前题目没有可展示的题干。", "")
    return pages[min(max(0, prompt_page), len(pages) - 1)]


def format_session_status(session: dict[str, Any]) -> str:
    """Summarize progress without retransmitting source text or hidden content."""
    snapshot = prepare_question_session_snapshot(session["snapshot"])
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
        prompt = snapshot["prompts"][prompt_index]
        stage = session.get("current_stage")
        page_field = "stem_pages"
        stage_label = "题干页"
        if stage == "answer":
            page_field = "answer_pages"
            stage_label = "答案页"
        elif stage == "explanation":
            page_field = "explanation_pages"
            stage_label = "解析页"
        pages = prompt.get(page_field, [])
        lines.append(
            f"{stage_label}：{session.get('current_prompt_page', 0) + 1}/{max(1, len(pages))}"
        )
    lines.append(
        "答案："
        f"{'已揭晓' if session.get('current_prompt_answer_revealed') else '未揭晓'}"
    )
    lines.append(
        "解析："
        f"{'已揭晓' if session.get('current_prompt_explanation_revealed') else '未揭晓'}"
    )
    return "\n".join(lines)


def format_session_answer(
    snapshot: dict[str, Any], *, prompt_index: int = 0, page_index: int = 0
) -> str:
    snapshot = prepare_question_session_snapshot(snapshot)
    prompts = snapshot.get("prompts", [])
    if not prompts:
        return "当前题目没有可用的参考答案。"
    prompt = prompts[min(max(0, prompt_index), len(prompts) - 1)]
    pages = prompt.get("answer_pages", [])
    if not pages:
        return _compose_message(_header(snapshot), "当前题目没有可用的参考答案。", "")
    return pages[min(max(0, page_index), len(pages) - 1)]


def format_session_explanation(
    snapshot: dict[str, Any], *, prompt_index: int = 0, page_index: int = 0
) -> str:
    snapshot = prepare_question_session_snapshot(snapshot)
    prompts = snapshot.get("prompts", [])
    if not prompts:
        return "当前题目没有可用解析。"
    pages = prompts[min(max(0, prompt_index), len(prompts) - 1)].get(
        "explanation_pages", []
    )
    if not pages:
        return _compose_message(_header(snapshot), "当前题目没有可用解析。", "")
    return pages[min(max(0, page_index), len(pages) - 1)]
