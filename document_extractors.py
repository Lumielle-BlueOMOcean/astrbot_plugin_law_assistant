from __future__ import annotations

import hashlib
import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_TEXT_CHARS = 600_000


class DocumentParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    index: int
    text: str
    locator: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    path: Path
    original_filename: str
    mime_type: str
    file_hash: str
    extracted_text_hash: str
    text: str
    segments: tuple[DocumentSegment, ...]
    warnings: tuple[str, ...] = ()
    parse_status: str = "parsed"


def extract_document(path: str | Path) -> ParsedDocument:
    file_path = Path(path)
    if not file_path.is_file():
        raise DocumentParseError("not_found", f"文件不存在：{file_path.name}")
    suffix = file_path.suffix.lower()
    if suffix not in {".txt", ".md", ".docx", ".pdf"}:
        raise DocumentParseError(
            "unsupported_format", f"不支持的文件格式：{suffix or '无扩展名'}"
        )
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise DocumentParseError("read_error", f"读取文件失败：{exc}") from exc
    if not data:
        raise DocumentParseError("empty_file", "文件为空")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentParseError("file_too_large", "文件超过 20 MB 限制")
    file_hash = hashlib.sha256(data).hexdigest()
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    if suffix in {".txt", ".md"}:
        text, segments, warnings = _extract_text(data)
    elif suffix == ".docx":
        text, segments, warnings = _extract_docx(data)
        mime_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    else:
        text, segments, warnings = _extract_pdf(data)
        mime_type = "application/pdf"
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentParseError("text_too_large", "提取文本超过 600000 字符限制")
    if not text.strip():
        raise DocumentParseError(
            "ocr_required", "文件没有可读取的文本，可能是扫描件或加密文档"
        )
    return ParsedDocument(
        path=file_path,
        original_filename=file_path.name,
        mime_type=mime_type,
        file_hash=file_hash,
        extracted_text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        segments=tuple(segments),
        warnings=tuple(warnings),
    )


def _extract_text(data: bytes) -> tuple[str, list[DocumentSegment], list[str]]:
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise DocumentParseError("decode_error", "无法按 UTF-8 或常见中文编码读取文本")
    lines = text.splitlines()
    segments = [
        DocumentSegment(index=index, text=line, locator=f"第{index + 1}行")
        for index, line in enumerate(lines)
        if line.strip()
    ]
    return text, segments, []


_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _extract_docx(data: bytes) -> tuple[str, list[DocumentSegment], list[str]]:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise DocumentParseError("corrupt_document", "DOCX 文件损坏或无法读取") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentParseError("corrupt_document", "DOCX 文档结构无法解析") from exc
    segments: list[DocumentSegment] = []
    body = root.find("w:body", _DOCX_NS)
    if body is None:
        return "", [], []
    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(child.itertext()).replace("\xa0", " ").strip()
            if text:
                style = child.find("w:pPr/w:pStyle", _DOCX_NS)
                style_name = (
                    style.get(f"{{{_DOCX_NS['w']}}}val") if style is not None else ""
                )
                locator = f"段落{len(segments) + 1}"
                if style_name and style_name.lower().startswith("heading"):
                    locator += f"/{style_name}"
                segments.append(DocumentSegment(len(segments), text, locator))
        elif tag == "tbl":
            for row_index, row in enumerate(child.findall("w:tr", _DOCX_NS), 1):
                cells = []
                for cell in row.findall("w:tc", _DOCX_NS):
                    cells.append(
                        " ".join(
                            "".join(p.itertext()).strip()
                            for p in cell.findall(".//w:p", _DOCX_NS)
                        ).strip()
                    )
                text = " | ".join(value for value in cells if value)
                if text:
                    segments.append(
                        DocumentSegment(len(segments), text, f"表格{row_index}行")
                    )
    text = "\n".join(segment.text for segment in segments)
    return text, segments, []


def _extract_pdf(data: bytes) -> tuple[str, list[DocumentSegment], list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentParseError(
            "dependency_missing", "缺少 pypdf，无法读取 PDF"
        ) from exc
    try:
        reader = PdfReader(__import__("io").BytesIO(data))
    except Exception as exc:  # pypdf has version-specific parsing exceptions.
        raise DocumentParseError("corrupt_document", "PDF 文件损坏或无法读取") from exc
    if reader.is_encrypted:
        raise DocumentParseError("encrypted_pdf", "PDF 已加密，无法在无密码情况下读取")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentParseError("page_limit", "PDF 超过 200 页限制")
    segments: list[DocumentSegment] = []
    for page_number, page in enumerate(reader.pages, 1):
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise DocumentParseError(
                "pdf_extract_error", f"第 {page_number} 页提取失败"
            ) from exc
        if page_text:
            segments.append(
                DocumentSegment(len(segments), page_text, f"第{page_number}页")
            )
    text = "\n".join(segment.text for segment in segments)
    warnings = [] if text.strip() else ["PDF 没有可提取文字，可能需要 OCR"]
    return text, segments, warnings


def first_locator(segments: tuple[DocumentSegment, ...] | list[DocumentSegment]) -> str:
    return segments[0].locator if segments else "未知位置"


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_TEXT_CHARS",
    "DocumentParseError",
    "DocumentSegment",
    "ParsedDocument",
    "extract_document",
    "first_locator",
]
