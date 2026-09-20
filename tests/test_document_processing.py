from __future__ import annotations

import io
import zipfile

import pytest

from document_extractors import DocumentParseError, extract_document
from document_ingestion import DocumentIngestionService
from learning_segmentation import segment_document, segment_official_cases
from library_repository import LibraryRepository
from library_service import LibraryService
from storage import SQLiteStorage


def _write_docx(path):
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:r><w:t>案例一：甲乙合同纠纷</w:t></w:r></w:p>
<w:p><w:r><w:t>甲与乙签订合同，双方发生争议，法院依法裁判。</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格证据</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def test_txt_preserves_line_locators_and_common_encoding(tmp_path):
    path = tmp_path / "材料.txt"
    path.write_bytes("第一行\n第二行".encode("gb18030"))

    document = extract_document(path)

    assert document.text == "第一行\n第二行"
    assert [item.locator for item in document.segments] == ["第1行", "第2行"]
    assert document.file_hash != document.extracted_text_hash


def test_docx_extracts_paragraphs_and_tables_without_executing_macros(tmp_path):
    path = tmp_path / "案例.docx"
    _write_docx(path)

    document = extract_document(path)

    assert "案例一" in document.text
    assert "表格证据" in document.text
    assert any("表格" in item.locator for item in document.segments)


def test_pdf_extracts_page_locators(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "empty.pdf"
    path.write_bytes(buffer.getvalue())

    with pytest.raises(DocumentParseError) as error:
        extract_document(path)
    assert error.value.code == "ocr_required"


def test_text_pdf_extracts_text_and_page_locator(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 180 Td (Page one text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "text.pdf"
    path.write_bytes(buffer.getvalue())

    document = extract_document(path)

    assert "Page one text" in document.text
    assert document.segments[0].locator == "第1页"


def test_multiple_questions_split_with_answers_and_locator(tmp_path):
    path = tmp_path / "试卷.txt"
    path.write_text(
        "第1题 单项选择题\n题干一\nA. 选项一\nB. 选项二\n"
        "第2题 多项选择题\n题干二\nA. 选项一\nB. 选项二\n"
        "答案\n1 A\n2 AB\n",
        encoding="utf-8",
    )
    document = extract_document(path)

    result = segment_document(document, content_kind="real_question_candidate")

    assert len(result.candidates) == 2
    assert result.candidates[0]["structured"]["answer"] == "A"
    assert result.candidates[1]["structured"]["question_type"] == "multiple_choice"
    assert result.candidates[0]["locator"] == "第1行-第4行"


def test_official_three_case_article_excludes_intro_and_keeps_independent_items(
    tmp_path,
):
    path = tmp_path / "官方合集.txt"
    path.write_text(
        "本期发布三个典型案例。\n"
        "案例一：知识产权纠纷\n甲公司主张商标侵权，法院作出裁判。\n"
        "案例二：合同纠纷\n乙公司与丙公司签订合同，法院认定违约。\n"
        "案例三：司法实务\n检察机关依法审查起诉并提出建议。\n",
        encoding="utf-8",
    )

    result = segment_official_cases(extract_document(path))

    assert len(result.candidates) == 3
    assert all(item["trusted_official"] is True for item in result.candidates)
    assert all("本期发布" not in item["raw_text"] for item in result.candidates)


def test_ambiguous_question_boundary_is_not_claimed_as_archived(tmp_path):
    path = tmp_path / "不完整.txt"
    path.write_text("题目提到甲乙两个案件但没有题号边界。", encoding="utf-8")

    result = segment_document(
        extract_document(path), content_kind="real_question_candidate"
    )

    assert result.candidates[0]["status"] == "needs_review"


@pytest.mark.asyncio
async def test_controlled_import_preserves_original_and_reloads_items(tmp_path):
    data_dir = tmp_path / "plugin-data"
    import_dir = data_dir / "imports"
    import_dir.mkdir(parents=True)
    path = import_dir / "试卷 with spaces.txt"
    path.write_text(
        "第1题 单项选择题\n题干一\nA. 选项一\nB. 选项二\n答案\n1 A\n",
        encoding="utf-8",
    )
    db = data_dir / "law.sqlite3"
    storage = SQLiteStorage(db)
    library = LibraryService(LibraryRepository(storage.connection))
    importer = DocumentIngestionService(data_dir, library)

    result = await importer.import_document(
        "试卷 with spaces.txt",
        created_by="42",
        session_origin="private:42",
        content_kind="real_question_candidate",
    )

    assert result["success"] is True
    assert result["archived"] == 1
    assert (data_dir / result["storage_path"]).is_file()
    source_id = result["source_id"]
    storage.close()

    reopened = SQLiteStorage(db)
    bundle = LibraryRepository(reopened.connection).get(result["items"][0]["item_id"])
    assert bundle is not None
    assert bundle.sources[0].id == source_id
    assert bundle.source_links[0].locator == "第1行-第6行"
    reopened.close()


@pytest.mark.asyncio
async def test_import_parse_failure_keeps_original_and_reports_error(tmp_path):
    data_dir = tmp_path / "plugin-data"
    import_dir = data_dir / "imports"
    import_dir.mkdir(parents=True)
    path = import_dir / "scan.pdf"
    path.write_bytes(b"not a pdf")
    storage = SQLiteStorage(data_dir / "law.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))

    result = await DocumentIngestionService(data_dir, library).import_document(
        "scan.pdf", created_by="42", session_origin="private:42"
    )

    assert result["success"] is False
    assert result["source_id"] is not None
    assert any(data_dir.joinpath("assets").iterdir())
    storage.close()
