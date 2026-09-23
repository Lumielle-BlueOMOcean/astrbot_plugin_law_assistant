from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class QuestionSessionRepository:
    """Persist scoped question-session state and append-only activity events."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_session(
        self,
        *,
        session_key: str,
        scope_origin: str,
        target_id: int | None,
        source_kind: str,
        source_item_key: str,
        library_item_id: int | None,
        real_question_id: int | None,
        question_identity: str,
        snapshot: dict[str, Any],
        created_by: str,
        created_at: str,
    ) -> dict[str, Any]:
        snapshot_json = _json(snapshot)
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        with self.connection:
            current = self.connection.execute(
                "SELECT id FROM question_sessions WHERE scope_origin = ? AND status = 'open'",
                (scope_origin,),
            ).fetchone()
            if current:
                self.connection.execute(
                    "UPDATE question_sessions SET status = 'superseded', closed_at = ?, updated_at = ? WHERE id = ?",
                    (created_at, created_at, current["id"]),
                )
                self._event(current["id"], "superseded", created_by, created_at)
            cursor = self.connection.execute(
                """INSERT INTO question_sessions(
                    session_key, scope_origin, target_id, source_kind, source_item_key,
                    library_item_id, real_question_id, question_identity,
                    question_snapshot_hash, question_snapshot_json, status, current_stage,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 'prompt', ?, ?, ?)""",
                (
                    session_key,
                    scope_origin,
                    target_id,
                    source_kind,
                    source_item_key,
                    library_item_id,
                    real_question_id,
                    question_identity,
                    snapshot_hash,
                    snapshot_json,
                    created_by,
                    created_at,
                    created_at,
                ),
            )
            session_id = int(cursor.lastrowid)
            self._event(session_id, "opened", created_by, created_at)
        result = self.get(session_id)
        assert result is not None
        return result

    def get(self, session_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM question_sessions WHERE id = ?", (int(session_id),)
        ).fetchone()
        return self._session_dict(row) if row else None

    def get_active(self, scope_origin: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM question_sessions WHERE scope_origin = ? AND status = 'open'",
            (scope_origin,),
        ).fetchone()
        return self._session_dict(row) if row else None

    def list_active(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM question_sessions WHERE status = 'open' ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [self._session_dict(row) for row in rows]

    def list_events(self, session_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM question_session_events WHERE session_id = ? ORDER BY id",
            (int(session_id),),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "session_id": int(row["session_id"]),
                "event_kind": str(row["event_kind"]),
                "actor_id": str(row["actor_id"]),
                "created_at": str(row["created_at"]),
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            for row in rows
        ]

    def reveal(
        self,
        session_id: int,
        kind: str,
        *,
        actor_id: str,
        at: str,
        prompt_index: int = 0,
    ) -> dict[str, Any] | None:
        if kind not in {"answer", "explanation"}:
            raise ValueError("kind must be answer or explanation")
        column = f"{kind}_revealed"
        event_kind = f"{kind}_revealed"
        with self.connection:
            row = self.connection.execute(
                f"SELECT status, {column} FROM question_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            if row is None:
                return None
            if row["status"] != "open":
                return self.get(session_id)
            event_rows = self.connection.execute(
                "SELECT metadata_json FROM question_session_events WHERE session_id = ? AND event_kind = ?",
                (int(session_id), event_kind),
            ).fetchall()
            already_recorded = any(
                int(json.loads(event["metadata_json"] or "{}").get("prompt_index", 0))
                == int(prompt_index)
                for event in event_rows
            )
            if not already_recorded:
                self.connection.execute(
                    f"UPDATE question_sessions SET {column} = 1, current_stage = ?, updated_at = ? WHERE id = ?",
                    (kind, at, int(session_id)),
                )
                self._event(
                    session_id,
                    event_kind,
                    actor_id,
                    at,
                    {"prompt_index": int(prompt_index)},
                )
            else:
                self.connection.execute(
                    f"UPDATE question_sessions SET {column} = 1, current_stage = ?, updated_at = ? WHERE id = ?",
                    (kind, at, int(session_id)),
                )
        return self.get(session_id)

    def advance(
        self,
        session_id: int,
        *,
        actor_id: str,
        at: str,
        material_index: int,
        prompt_index: int,
        stage: str = "prompt",
        material_page: int = 0,
        prompt_page: int = 0,
    ) -> dict[str, Any] | None:
        if stage not in {"prompt", "answer", "explanation", "complete"}:
            raise ValueError("unsupported session stage")
        with self.connection:
            row = self.connection.execute(
                "SELECT status FROM question_sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
            if row is None or row["status"] != "open":
                return self.get(session_id) if row else None
            self.connection.execute(
                """UPDATE question_sessions SET current_material_index = ?,
                    current_prompt_index = ?, current_stage = ?, current_material_page = ?,
                    current_prompt_page = ?, updated_at = ? WHERE id = ?""",
                (
                    material_index,
                    prompt_index,
                    stage,
                    material_page,
                    prompt_page,
                    at,
                    int(session_id),
                ),
            )
            self._event(
                session_id,
                "continued",
                actor_id,
                at,
                {
                    "material_index": material_index,
                    "material_page": material_page,
                    "prompt_index": prompt_index,
                    "prompt_page": prompt_page,
                },
            )
        return self.get(session_id)

    def close(self, session_id: int, *, actor_id: str, at: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE question_sessions SET status = 'closed', current_stage = 'complete',
                    closed_at = ?, updated_at = ? WHERE id = ? AND status = 'open'""",
                (at, at, int(session_id)),
            )
            if cursor.rowcount:
                self._event(session_id, "closed", actor_id, at)
                return True
            return False

    def _event(
        self,
        session_id: int,
        kind: str,
        actor_id: str,
        at: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO question_session_events(
                session_id, event_kind, actor_id, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?)""",
            (int(session_id), kind, str(actor_id), at, _json(metadata or {})),
        )

    @staticmethod
    def _session_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "session_key": str(row["session_key"]),
            "scope_origin": str(row["scope_origin"]),
            "target_id": row["target_id"],
            "source_kind": str(row["source_kind"]),
            "source_item_key": str(row["source_item_key"]),
            "library_item_id": row["library_item_id"],
            "real_question_id": row["real_question_id"],
            "question_identity": str(row["question_identity"]),
            "snapshot_hash": str(row["question_snapshot_hash"]),
            "snapshot": json.loads(row["question_snapshot_json"]),
            "status": str(row["status"]),
            "current_stage": str(row["current_stage"]),
            "current_material_index": int(row["current_material_index"]),
            "current_material_page": int(row["current_material_page"]),
            "current_prompt_index": int(row["current_prompt_index"]),
            "current_prompt_page": int(row["current_prompt_page"]),
            "answer_revealed": bool(row["answer_revealed"]),
            "explanation_revealed": bool(row["explanation_revealed"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "closed_at": row["closed_at"],
        }
