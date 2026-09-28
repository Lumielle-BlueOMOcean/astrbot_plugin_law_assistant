"""Shared bounded pagination contracts for dashboard list endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PAGE_SIZES = (20, 50, 100)


def page_request(page: Any = 1, page_size: Any = 20) -> tuple[int, int, int]:
    try:
        normalized_page = max(1, int(page))
        requested_size = int(page_size)
    except (TypeError, ValueError) as exc:
        raise ValueError("page 和 page_size 必须是整数") from exc
    if requested_size not in PAGE_SIZES:
        raise ValueError("page_size 必须是 20、50 或 100")
    return normalized_page, requested_size, (normalized_page - 1) * requested_size


def page_payload(
    items: Iterable[Any], *, page: int, page_size: int, total: int
) -> dict[str, Any]:
    count = max(0, int(total))
    return {
        "items": list(items),
        "page": int(page),
        "page_size": int(page_size),
        "total": count,
        "page_count": (count + int(page_size) - 1) // int(page_size),
    }


__all__ = ["PAGE_SIZES", "page_payload", "page_request"]
