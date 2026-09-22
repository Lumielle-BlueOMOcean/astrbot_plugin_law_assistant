"""Controlled prepare/confirm handling for structured material imports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .document_extractors import extract_document
    from .library_service import LibraryService
    from .structured_material import (
        MAX_JSON_BYTES,
        build_structured_material_preview,
        validate_structured_material,
    )
except ImportError:
    from document_extractors import extract_document
    from library_service import LibraryService
    from structured_material import (
        MAX_JSON_BYTES,
        build_structured_material_preview,
        validate_structured_material,
    )


class StructuredImportError(ValueError):
    """A user-correctable structured import failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedStructuredImport:
    original_path: Path
    structured_path: Path
    original_filename: str
    structured_filename: str
    original_file_sha256: str
    structured_json_sha256: str
    structured_payload_sha256: str
    original_mime_type: str
    original_text: str
    payload: dict[str, Any]
    validation: Any
    preview: dict[str, Any]
    created_by: str
    session_origin: str


class StructuredMaterialIngestionService:
    """Keep structured JSON separate from legacy text segmentation."""

    def __init__(
        self,
        data_dir: str | Path,
        library_service: LibraryService,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.import_dir = self.data_dir / "imports"
        self.asset_dir = self.data_dir / "assets"
        self.library_service = library_service

    async def stage_json_upload(self, filename: str, data: bytes) -> dict[str, Any]:
        original = Path(str(filename or "").replace("\\", "/")).name
        suffix = Path(original).suffix.lower()
        if suffix != ".json":
            raise ValueError("结构化资料必须上传 .json 文件")
        if not data:
            raise ValueError("JSON 文件为空")
        if len(data) > MAX_JSON_BYTES:
            raise ValueError("JSON 文件超过 10 MB 限制")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("结构化 JSON 必须使用 UTF-8 编码") from exc
        digest = hashlib.sha256(data).hexdigest()
        self.import_dir.mkdir(parents=True, exist_ok=True)
        path = self.import_dir / f"{digest}.json"
        if path.exists() and path.is_symlink():
            raise ValueError("staged JSON 路径无效")
        if not path.exists():
            await asyncio.to_thread(path.write_bytes, data)
        return {
            "staged_path": path.relative_to(self.import_dir).as_posix(),
            "original_filename": original,
            "file_hash": digest,
            "size": len(data),
            "extension": suffix,
        }

    async def prepare(
        self,
        original_staged_path: str,
        structured_staged_path: str,
        *,
        created_by: str,
        session_origin: str,
        original_filename: str | None = None,
        structured_filename: str | None = None,
    ) -> PreparedStructuredImport:
        original_path = self._resolve(original_staged_path, allow_json=False)
        structured_path = self._resolve(structured_staged_path, allow_json=True)
        if original_path.suffix.lower() != ".pdf":
            raise StructuredImportError(
                "unsupported_format", "Phase 0.4B 结构化导入当前要求原始文件为 PDF"
            )
        original_bytes = await asyncio.to_thread(original_path.read_bytes)
        structured_bytes = await asyncio.to_thread(structured_path.read_bytes)
        prepared = await self._prepare_from_bytes(
            original_path,
            structured_path,
            original_bytes,
            structured_bytes,
            created_by=created_by,
            session_origin=session_origin,
            original_filename=original_filename,
            structured_filename=structured_filename,
        )
        return prepared

    async def confirm(self, prepared: PreparedStructuredImport) -> dict[str, Any]:
        original_bytes = await asyncio.to_thread(prepared.original_path.read_bytes)
        structured_bytes = await asyncio.to_thread(prepared.structured_path.read_bytes)
        current = await self._prepare_from_bytes(
            prepared.original_path,
            prepared.structured_path,
            original_bytes,
            structured_bytes,
            created_by=prepared.created_by,
            session_origin=prepared.session_origin,
            original_filename=prepared.original_filename,
            structured_filename=prepared.structured_filename,
        )
        if (
            current.original_file_sha256 != prepared.original_file_sha256
            or current.structured_json_sha256 != prepared.structured_json_sha256
            or current.structured_payload_sha256 != prepared.structured_payload_sha256
            or current.payload.get("schema_version")
            != prepared.payload.get("schema_version")
        ):
            raise StructuredImportError(
                "staged_file_changed", "原始文件或结构化 JSON 已变化，请重新 prepare"
            )
        original_asset = self._preserve_asset(
            current.original_path,
            current.original_file_sha256,
        )
        structured_asset = self._preserve_asset(
            current.structured_path,
            f"structured-{current.structured_json_sha256}",
        )
        result = self.library_service.archive_structured_material(
            payload=current.payload,
            validation=current.validation,
            original_filename=current.original_filename,
            original_mime_type=current.original_mime_type,
            original_storage_path=str(original_asset.relative_to(self.data_dir)),
            original_text=current.original_text,
            original_file_sha256=current.original_file_sha256,
            structured_json_text=structured_bytes.decode("utf-8"),
            structured_storage_path=str(structured_asset.relative_to(self.data_dir)),
            structured_json_sha256=current.structured_json_sha256,
            structured_payload_sha256=current.structured_payload_sha256,
            created_by=current.created_by,
            session_origin=current.session_origin,
            now=datetime.now(timezone.utc),
        )
        result.update(
            {
                "success": True,
                "original_filename": current.original_filename,
                "structured_filename": current.structured_filename,
                "original_file_sha256": current.original_file_sha256,
                "structured_json_sha256": current.structured_json_sha256,
                "structured_payload_sha256": current.structured_payload_sha256,
                "storage_path": str(original_asset.relative_to(self.data_dir)),
            }
        )
        return result

    async def _prepare_from_bytes(
        self,
        original_path: Path,
        structured_path: Path,
        original_bytes: bytes,
        structured_bytes: bytes,
        *,
        created_by: str,
        session_origin: str,
        original_filename: str | None,
        structured_filename: str | None,
    ) -> PreparedStructuredImport:
        if len(structured_bytes) > MAX_JSON_BYTES:
            raise StructuredImportError(
                "json_too_large", "结构化 JSON 文件超过 10 MB 限制"
            )
        original_hash = hashlib.sha256(original_bytes).hexdigest()
        json_hash = hashlib.sha256(structured_bytes).hexdigest()
        try:
            structured_text = structured_bytes.decode("utf-8")
            payload = json.loads(structured_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StructuredImportError(
                "invalid_json", "结构化 JSON 必须是合法 UTF-8 JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise StructuredImportError(
                "fatal_validation", "结构化 JSON 根节点必须是对象"
            )
        validation = validate_structured_material(payload)
        declared_hash = validation.document.get("original_file_sha256")
        if declared_hash != original_hash:
            raise StructuredImportError(
                "document_hash_mismatch",
                "structured JSON 中的 original_file_sha256 与原始文件不一致",
            )
        if validation.fatal:
            messages = "；".join(
                issue.message
                for issue in validation.issues
                if issue.severity == "fatal"
            )
            raise StructuredImportError(
                "fatal_validation", messages or "结构化资料校验失败"
            )
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        parsed = await asyncio.to_thread(extract_document, original_path)
        preview = build_structured_material_preview(payload)
        preview["original_file_sha256"] = original_hash
        preview["structured_json_sha256"] = json_hash
        preview["structured_payload_sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        preview["original_filename"] = str(original_filename or original_path.name)
        preview["structured_filename"] = str(
            structured_filename or structured_path.name
        )
        preview["fatal"] = sum(issue.severity == "fatal" for issue in validation.issues)
        preview["errors"] = sum(
            issue.severity == "error" for issue in validation.issues
        )
        preview["review"] = sum(
            issue.severity == "review" for issue in validation.issues
        )
        return PreparedStructuredImport(
            original_path=original_path,
            structured_path=structured_path,
            original_filename=str(original_filename or original_path.name),
            structured_filename=str(structured_filename or structured_path.name),
            original_file_sha256=original_hash,
            structured_json_sha256=json_hash,
            structured_payload_sha256=hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            original_mime_type="application/pdf",
            original_text=parsed.text,
            payload=payload,
            validation=validation,
            preview=preview,
            created_by=str(created_by),
            session_origin=str(session_origin),
        )

    def _resolve(self, relative_path: str, *, allow_json: bool) -> Path:
        value = str(relative_path or "").strip()
        if not value:
            raise StructuredImportError(
                "invalid_path", "必须提供 imports 目录内的相对路径"
            )
        candidate = Path(value)
        if candidate.is_absolute():
            raise StructuredImportError("invalid_path", "不允许使用绝对路径")
        root = self.import_dir.resolve()
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise StructuredImportError(
                "invalid_path", "路径必须位于 imports 目录内"
            ) from exc
        suffix = resolved.suffix.lower()
        allowed = {".json"} if allow_json else {".pdf", ".docx", ".txt", ".md"}
        if suffix not in allowed:
            raise StructuredImportError(
                "unsupported_format", "不支持的结构化导入文件格式"
            )
        if resolved.is_symlink() or not resolved.is_file():
            raise StructuredImportError("invalid_path", "staged 文件不存在或路径无效")
        return resolved

    def _preserve_asset(self, path: Path, stem: str) -> Path:
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        target = self.asset_dir / f"{stem}{path.suffix.lower()}"
        if not target.exists():
            shutil.copyfile(path, target)
        return target


__all__ = [
    "PreparedStructuredImport",
    "StructuredImportError",
    "StructuredMaterialIngestionService",
]
