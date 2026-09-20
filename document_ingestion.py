from __future__ import annotations

import asyncio
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ and "." in __package__:
    from .document_extractors import (
        DocumentParseError,
        ParsedDocument,
        extract_document,
    )
    from .learning_segmentation import MAX_CANDIDATES, segment_document
    from .library_models import LibrarySource
    from .library_service import LibraryService
else:
    from document_extractors import DocumentParseError, ParsedDocument, extract_document
    from learning_segmentation import MAX_CANDIDATES, segment_document
    from library_models import LibrarySource
    from library_service import LibraryService


class DocumentIngestionService:
    """Import documents from the plugin-owned controlled directory."""

    def __init__(
        self,
        data_dir: str | Path,
        library_service: LibraryService,
        *,
        clock: Any | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.import_dir = self.data_dir / "imports"
        self.asset_dir = self.data_dir / "assets"
        self.library_service = library_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def import_document(
        self,
        relative_path: str,
        *,
        created_by: str,
        session_origin: str,
        content_kind: str = "auto",
    ) -> dict[str, Any]:
        try:
            path = self._resolve_import_path(relative_path)
        except ValueError as exc:
            return {"success": False, "error": "invalid_path", "reason": str(exc)}
        try:
            parsed = await asyncio.to_thread(extract_document, path)
        except DocumentParseError as exc:
            source_id = await self._record_failed_source(
                path,
                created_by=created_by,
                session_origin=session_origin,
                error=exc,
            )
            return {
                "success": False,
                "error": exc.code,
                "reason": str(exc),
                "source_id": source_id,
                "original_filename": path.name,
            }
        asset_path = await asyncio.to_thread(self._preserve_original, parsed)
        segments = segment_document(
            parsed, content_kind=content_kind, max_candidates=MAX_CANDIDATES
        )
        source = self._source_from_parsed(
            parsed,
            asset_path=asset_path,
            created_by=created_by,
            session_origin=session_origin,
            content_kind=content_kind,
            warnings=segments.warnings,
        )
        result = await self.library_service.archive_material_batch(
            source=source,
            candidates=list(segments.candidates),
        )
        result.update(
            {
                "original_filename": parsed.original_filename,
                "file_hash": parsed.file_hash,
                "extracted_text_hash": parsed.extracted_text_hash,
                "parse_status": parsed.parse_status,
                "warnings": list(parsed.warnings) + list(segments.warnings),
                "storage_path": str(asset_path.relative_to(self.data_dir)),
            }
        )
        return result

    def _resolve_import_path(self, relative_path: str) -> Path:
        value = str(relative_path or "").strip()
        if not value:
            raise ValueError("必须提供受控导入目录内的相对路径")
        candidate = Path(value)
        if candidate.is_absolute():
            raise ValueError("不允许使用绝对路径")
        root = self.import_dir.resolve()
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("路径必须位于插件 imports 目录内") from exc
        return resolved

    def _preserve_original(self, document: ParsedDocument) -> Path:
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        target = self.asset_dir / f"{document.file_hash}{document.path.suffix.lower()}"
        if not target.exists():
            shutil.copyfile(document.path, target)
        return target

    def _source_from_parsed(
        self,
        document: ParsedDocument,
        *,
        asset_path: Path,
        created_by: str,
        session_origin: str,
        content_kind: str,
        warnings: tuple[str, ...] | list[str],
    ) -> LibrarySource:
        return LibrarySource(
            source_kind="file",
            title=document.original_filename,
            raw_text=document.text,
            source_url="",
            content_hash=document.extracted_text_hash,
            created_at=self.clock(),
            created_by=str(created_by),
            session_origin=str(session_origin),
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            storage_path=str(asset_path.relative_to(self.data_dir)),
            metadata={
                "file_hash": document.file_hash,
                "extracted_text_hash": document.extracted_text_hash,
                "parse_status": document.parse_status,
                "parse_warnings": list(document.warnings) + list(warnings),
                "content_kind": content_kind,
            },
        )

    async def _record_failed_source(
        self,
        path: Path,
        *,
        created_by: str,
        session_origin: str,
        error: DocumentParseError,
    ) -> int | None:
        try:
            data = await asyncio.to_thread(path.read_bytes)
            file_hash = hashlib.sha256(data).hexdigest()
            asset_dir = self.asset_dir
            asset_dir.mkdir(parents=True, exist_ok=True)
            target = asset_dir / f"{file_hash}{path.suffix.lower()}"
            if not target.exists():
                await asyncio.to_thread(shutil.copyfile, path, target)
            source = LibrarySource(
                source_kind="file",
                title=path.name,
                raw_text="",
                source_url="",
                content_hash=file_hash,
                created_at=self.clock(),
                created_by=str(created_by),
                session_origin=str(session_origin),
                original_filename=path.name,
                mime_type="application/octet-stream",
                storage_path=str(target.relative_to(self.data_dir)),
                metadata={
                    "file_hash": file_hash,
                    "parse_status": "failed",
                    "parse_error": error.code,
                    "parse_warnings": [str(error)],
                },
            )
            return self.library_service.record_source(source)
        except (OSError, ValueError):
            return None


__all__ = ["DocumentIngestionService"]
