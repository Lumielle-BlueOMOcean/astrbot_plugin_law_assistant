from __future__ import annotations

from typing import Any


class ContentTooLongError(ValueError):
    """Raised when a learning card cannot fit its configured reading budget."""


def format_case_card(content: dict[str, Any], *, max_chars: int = 1800) -> str:
    """Format one independent case without truncating evidence-bearing sections."""
    body = content.get("content", content)
    if not isinstance(body, dict):
        body = {"case_summary": str(body)}
    subject = content.get("subject") or ""
    lines = [f"【每日一案{f'｜{subject}' if subject else ''}】"]
    if content.get("title"):
        lines.append(f"案例：{content['title']}")
    if content.get("authority"):
        lines.append(f"来源机关：{content['authority']}")
    labels = {
        "case_summary": "核心事实",
        "issues": "争议焦点",
        "reasoning": "裁判／检察要旨",
        "practice_notes": "学习要点",
        "evidence_text": "原文证据",
    }
    for key in ("case_summary", "issues", "reasoning", "practice_notes"):
        value = body.get(key)
        if value in (None, "", [], ()):
            continue
        if isinstance(value, (list, tuple)):
            value = "；".join(str(item) for item in value)
        lines.append(f"{labels[key]}：{value}")
    if content.get("source_locator"):
        lines.append(f"原文位置：{content['source_locator']}")
    if content.get("source_url"):
        lines.append(f"官方来源：{content['source_url']}")
    result = "\n".join(lines).strip()
    if len(result) > max_chars:
        raise ContentTooLongError(
            f"独立案例学习卡片长度为 {len(result)}，超过 {max_chars} 字符预算"
        )
    return result


__all__ = ["ContentTooLongError", "format_case_card"]
