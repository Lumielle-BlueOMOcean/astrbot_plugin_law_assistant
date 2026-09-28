from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

_REVEAL_KINDS = {"answer", "explanation"}
_TERMINAL_STATUSES = {"sent", "failed", "skipped", "needs_review"}


class ScheduledRevealRepository:
    """Persist one-shot staged reveals bound to a successful question session."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_for_session(
        self,
        *,
        session_id: int,
        target_umo: str,
        snapshot_hash: str,
        question_sent_at: str,
        due_at: str,
        reveal_kind: str,
        created_at: str,
        prompt_index: int = 0,
    ) -> dict[str, Any]:
        if reveal_kind not in _REVEAL_KINDS:
            raise ValueError("reveal_kind must be answer or explanation")
        if int(prompt_index) < 0:
            raise ValueError("prompt_index must be non-negative")
        for name, value in (
            ("question_sent_at", question_sent_at),
            ("due_at", due_at),
            ("created_at", created_at),
        ):
            try:
                datetime.fromisoformat(str(value))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be an ISO datetime") from exc

        with self.connection:
            session = self.connection.execute(
                "SELECT target_id, scope_origin, question_snapshot_hash, status "
                "FROM question_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            if session is None or session["status"] != "open":
                raise ValueError("scheduled reveal requires an open question session")
            if str(session["scope_origin"]) != str(target_umo):
                raise ValueError("target UMO does not match question session scope")
            if str(session["question_snapshot_hash"]) != str(snapshot_hash):
                raise ValueError("snapshot hash does not match question session")

            existing = self.connection.execute(
                "SELECT * FROM scheduled_reveal_jobs "
                "WHERE session_id = ? AND reveal_kind = ? AND prompt_index = ?",
                (int(session_id), reveal_kind, int(prompt_index)),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["target_umo"]) != str(target_umo)
                    or str(existing["snapshot_hash"]) != str(snapshot_hash)
                    or str(existing["question_sent_at"]) != str(question_sent_at)
                    or str(existing["due_at"]) != str(due_at)
                    or int(existing["prompt_index"]) != int(prompt_index)
                ):
                    raise ValueError("conflicting scheduled reveal snapshot")
                return self._as_dict(existing)

            cursor = self.connection.execute(
                """INSERT INTO scheduled_reveal_jobs(
                    session_id, target_id, target_umo, snapshot_hash,
                    question_sent_at, due_at, reveal_kind, prompt_index,
                    status, page_progress_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '[]', ?, ?)""",
                (
                    int(session_id),
                    session["target_id"],
                    str(target_umo),
                    str(snapshot_hash),
                    str(question_sent_at),
                    str(due_at),
                    reveal_kind,
                    int(prompt_index),
                    str(created_at),
                    str(created_at),
                ),
            )
            row = self.connection.execute(
                "SELECT * FROM scheduled_reveal_jobs WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            return self._as_dict(row)

    def get(self, job_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM scheduled_reveal_jobs WHERE id = ?", (int(job_id),)
        ).fetchone()
        return self._as_dict(row) if row else None

    def list_pending(
        self, *, now: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "status = 'pending'"
        if now is not None:
            where += " AND due_at <= ?"
            params.append(str(now))
        params.append(max(1, min(int(limit), 1000)))
        rows = self.connection.execute(
            f"SELECT * FROM scheduled_reveal_jobs WHERE {where} "
            "ORDER BY due_at, id LIMIT ?",
            params,
        ).fetchall()
        return [self._as_dict(row) for row in rows]

    def list_history(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return delivery diagnostics only; answer text is never stored here."""
        rows = self.connection.execute(
            "SELECT id, session_id, target_id, target_umo, snapshot_hash, "
            "question_sent_at, due_at, reveal_kind, prompt_index, status, "
            "attempt_count, attempted_at, finished_at, page_progress_json, "
            "error_summary, created_at, updated_at "
            "FROM scheduled_reveal_jobs ORDER BY id DESC LIMIT ? OFFSET ?",
            (max(1, min(int(limit), 200)), max(0, int(offset))),
        ).fetchall()
        return [self._as_dict(row) for row in rows]

    def claim_due(self, job_id: int, *, now: str) -> dict[str, Any] | None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE scheduled_reveal_jobs SET status = 'sending',
                    attempt_count = attempt_count + 1, attempted_at = ?,
                    updated_at = ? WHERE id = ? AND status = 'pending' AND due_at <= ?""",
                (str(now), str(now), int(job_id), str(now)),
            )
            if cursor.rowcount != 1:
                return None
            row = self.connection.execute(
                "SELECT * FROM scheduled_reveal_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
            if row is None:
                return None
            return self._as_dict(row)

    def mark_page_sent(self, job_id: int, page_index: int, *, at: str) -> bool:
        page_index = int(page_index)
        if page_index < 0:
            raise ValueError("page_index must be non-negative")
        with self.connection:
            row = self.connection.execute(
                "SELECT status, page_progress_json FROM scheduled_reveal_jobs "
                "WHERE id = ?",
                (int(job_id),),
            ).fetchone()
            if row is None or row["status"] != "sending":
                return False
            progress = [int(value) for value in json.loads(row["page_progress_json"])]
            if page_index in progress:
                return True
            if page_index != len(progress):
                raise ValueError("pages must be recorded in send order")
            progress.append(page_index)
            self.connection.execute(
                "UPDATE scheduled_reveal_jobs SET page_progress_json = ?, "
                "updated_at = ? WHERE id = ? AND status = 'sending'",
                (
                    json.dumps(progress, separators=(",", ":")),
                    str(at),
                    int(job_id),
                ),
            )
            return True

    def finish(
        self,
        job_id: int,
        status: str,
        *,
        at: str,
        error_summary: str | None = None,
    ) -> bool:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("finish status must be terminal")
        with self.connection:
            row = self.connection.execute(
                "SELECT page_progress_json FROM scheduled_reveal_jobs "
                "WHERE id = ? AND status = 'sending'",
                (int(job_id),),
            ).fetchone()
            if row is None:
                return False
            if status == "sent" and not json.loads(row["page_progress_json"]):
                raise ValueError("a sent reveal must have at least one delivered page")
            self.connection.execute(
                """UPDATE scheduled_reveal_jobs SET status = ?, finished_at = ?,
                    updated_at = ?, error_summary = ?
                    WHERE id = ? AND status = 'sending'""",
                (
                    status,
                    str(at),
                    str(at),
                    str(error_summary)[:500] if error_summary else None,
                    int(job_id),
                ),
            )
            return True

    def skip(self, job_id: int, *, at: str, reason: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE scheduled_reveal_jobs SET status = 'skipped',
                    finished_at = ?, updated_at = ?, error_summary = ?
                    WHERE id = ? AND status = 'pending'""",
                (str(at), str(at), str(reason)[:500], int(job_id)),
            )
            return cursor.rowcount == 1

    def skip_for_manual_reveal(
        self,
        session_id: int,
        reveal_kind: str,
        *,
        prompt_index: int,
        at: str,
    ) -> int:
        if reveal_kind not in _REVEAL_KINDS:
            raise ValueError("reveal_kind must be answer or explanation")
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE scheduled_reveal_jobs SET status = 'skipped',
                    finished_at = ?, updated_at = ?,
                    error_summary = '已由人工提前揭晓'
                    WHERE session_id = ? AND reveal_kind = ? AND prompt_index = ?
                        AND status = 'pending'""",
                (str(at), str(at), int(session_id), reveal_kind, int(prompt_index)),
            )
            return cursor.rowcount

    def was_revealed(
        self, session_id: int, reveal_kind: str, prompt_index: int
    ) -> bool:
        if reveal_kind not in _REVEAL_KINDS:
            raise ValueError("reveal_kind must be answer or explanation")
        rows = self.connection.execute(
            "SELECT metadata_json FROM question_session_events "
            "WHERE session_id = ? AND event_kind = ?",
            (int(session_id), f"{reveal_kind}_revealed"),
        ).fetchall()
        return any(
            int(json.loads(row["metadata_json"] or "{}").get("prompt_index", 0))
            == int(prompt_index)
            for row in rows
        )

    def recover_inflight(self, *, at: str) -> int:
        """Quarantine uncertain sends after restart; never blindly replay them."""
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE scheduled_reveal_jobs SET status = 'needs_review',
                    finished_at = ?, updated_at = ?,
                    error_summary = '进程在发送过程中退出，送达状态需人工核验'
                    WHERE status = 'sending'""",
                (str(at), str(at)),
            )
            return cursor.rowcount

    @staticmethod
    def _as_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["page_progress"] = json.loads(result.pop("page_progress_json") or "[]")
        return result


__all__ = ["ScheduledRevealRepository"]
