from __future__ import annotations

import json

import pytest

from structured_material import (
    MAX_NESTING_DEPTH,
    MAX_TOTAL_TEXT_CHARS,
    build_structured_material_preview,
    match_numbered_answer_entries,
    validate_structured_material,
)


def _block(
    block_id: str,
    order: int,
    text: str,
    *,
    kind: str = "paragraph",
    locator: str = "第1页",
    provenance: str = "source_text",
) -> dict:
    return {
        "id": block_id,
        "order": order,
        "kind": kind,
        "text": text,
        "locator": locator,
        "provenance": provenance,
    }


def _question(
    question_id: str = "Q001",
    *,
    question_type: str = "single_choice",
    source_number: str = "第1题",
    answer: object = None,
    answer_status: str = "provided",
    answer_reason: str = "",
    material_refs: list[str] | None = None,
    stem_blocks: list[dict] | None = None,
    options: list[dict] | None = None,
    explanation_blocks: list[dict] | None = None,
    subquestions: list[dict] | None = None,
    verification_status: str = "pending_review",
) -> dict:
    if answer is None and answer_status == "provided":
        answer = {
            "keys": ["A"],
            "status": "provided",
            "provenance": "source_text",
            "locator": "第1页",
        }
    return {
        "id": question_id,
        "source_number": source_number,
        "question_type": question_type,
        "material_refs": material_refs or [],
        "stem_blocks": stem_blocks
        or [_block(f"{question_id}-S01", 1, "甲乙之间发生一项虚构法律争议。")],
        "options": options
        if options is not None
        else [
            {"key": "A", "text": "甲说法成立", "locator": "第1页"},
            {"key": "B", "text": "乙说法成立", "locator": "第1页"},
        ],
        "subquestions": subquestions or [],
        "answer": answer,
        "answer_status": answer_status,
        "answer_reason": answer_reason,
        "explanation_blocks": explanation_blocks
        or [
            _block(
                f"{question_id}-E01",
                1,
                "这是虚构样本的学习解析。",
                provenance="human_authored",
            )
        ],
        "locators": ["第1页"],
        "verification_status": verification_status,
    }


def _valid_document() -> dict:
    material = {
        "id": "M01",
        "title": "虚构公共材料",
        "blocks": [
            _block("M01-B01", 1, "这是仅用于测试的虚构公共材料。"),
            _block(
                "M01-B02",
                2,
                "图片尚未完整转录。",
                kind="image_reference",
                provenance="unknown",
            ),
        ],
    }
    question = _question(
        material_refs=["M01"],
        answer={
            "keys": ["A"],
            "status": "provided",
            "provenance": "source_text",
            "locator": "第1页",
        },
    )
    case = {
        "id": "C001",
        "title": "虚构合同案例",
        "case_number": "虚构案号-001",
        "authority": "测试机构（非官方声明）",
        "verification_status": "pending_review",
        "basic_facts_blocks": [_block("C001-F01", 1, "这是人工编写的虚构案情。")],
        "issues_blocks": [_block("C001-I01", 1, "合同责任如何判断？")],
        "holding_blocks": [
            _block(
                "C001-H01",
                1,
                "本样本不表达任何真实裁判结果。",
                provenance="human_authored",
            )
        ],
        "result_blocks": [],
        "learning_points_blocks": [
            _block(
                "C001-L01", 1, "仅用于验证结构化资料格式。", provenance="human_authored"
            )
        ],
        "locators": ["第2页"],
    }
    return {
        "schema_version": "1.0",
        "document": {
            "title": "虚构结构化资料样本",
            "source_type": "synthetic_fixture",
            "original_filename": "synthetic-material.txt",
            "original_file_sha256": "a" * 64,
            "source_url": "",
            "source_description": "人工编写，仅用于确定性测试",
            "exam_name": "虚构练习资料",
            "exam_year": "2099",
            "paper": "样本卷",
            "preparation_method": "human_transcribed",
            "verification_status": "pending_review",
        },
        "materials": [material],
        "questions": [question],
        "cases": [case],
    }


def _issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_valid_document_preview_preserves_order_provenance_and_counts():
    result = validate_structured_material(_valid_document())

    assert result.fatal is False
    assert result.usable_questions == 1
    assert result.usable_cases == 1
    assert result.review_items == 0
    assert result.materials[0]["blocks"][1]["kind"] == "image_reference"
    assert result.questions[0]["answer"]["provenance"] == "source_text"

    preview = build_structured_material_preview(_valid_document())
    assert preview["schema_version"] == "1.0"
    assert preview["document"]["title"] == "虚构结构化资料样本"
    assert preview["counts"] == {
        "questions": 1,
        "cases": 1,
        "materials": 1,
        "subquestions": 0,
        "answer_requirements": 0,
        "answer_mappings_resolved": 0,
        "answer_mappings_unresolved": 0,
        "duplicate_warnings": 0,
        "contamination_warnings": 0,
        "processable": 2,
        "entry_errors": 0,
        "review_items": 0,
    }
    assert preview["questions"][0]["stem_block_count"] == 1
    assert preview["cases"][0]["title"] == "虚构合同案例"


def test_json_text_is_parsed_and_unknown_schema_is_fatal():
    result = validate_structured_material(
        json.dumps(_valid_document(), ensure_ascii=False)
    )
    assert result.fatal is False

    unknown = _valid_document()
    unknown["schema_version"] = "2.0"
    result = validate_structured_material(unknown)
    assert result.fatal is True
    assert "schema.unsupported_version" in _issue_codes(result)


@pytest.mark.parametrize(
    ("question_type", "answer", "options"),
    [
        ("single_choice", {"keys": ["A"], "provenance": "source_text"}, None),
        ("multiple_choice", {"keys": ["A", "B"], "provenance": "source_text"}, None),
        ("true_false", {"value": True, "provenance": "source_text"}, []),
        ("short_answer", None, []),
        (
            "case_analysis",
            {"blocks": [_block("A01", 1, "参考答案", provenance="human_authored")]},
            [],
        ),
    ],
)
def test_supported_question_types_keep_answer_shapes(question_type, answer, options):
    payload = _valid_document()
    payload["questions"] = [
        _question(
            question_type=question_type,
            answer=answer,
            answer_status="not_provided" if answer is None else "provided",
            answer_reason="原文没有参考答案" if answer is None else "",
            options=options,
        )
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_questions == 1


def test_shared_material_can_have_three_independent_subquestions():
    subquestions = []
    for index in range(1, 4):
        subquestions.append(
            {
                "id": f"Q001-S{index:02d}",
                "source_number": f"（{index}）",
                "stem_blocks": [
                    _block(f"Q001-S{index:02d}-B01", 1, f"第{index}个独立小问。")
                ],
                "answer": {
                    "blocks": [
                        _block(
                            f"Q001-S{index:02d}-A01",
                            1,
                            "参考答案",
                            provenance="human_authored",
                        )
                    ],
                    "provenance": "human_authored",
                },
                "explanation_blocks": [],
                "locators": [f"第{index + 1}页"],
            }
        )
    payload = _valid_document()
    payload["questions"] = [
        _question(
            question_type="case_analysis",
            answer=None,
            answer_status="not_provided",
            answer_reason="主问题没有独立参考答案",
            options=[],
            stem_blocks=[_block("Q001-MAIN-S01", 1, "这是一道公共材料主问题。")],
            subquestions=subquestions,
        )
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_questions == 1
    assert result.questions[0]["subquestions"][2]["id"] == "Q001-S03"


def test_essay_requirements_are_not_subquestions_and_preview_counts_them():
    payload = _valid_document()
    payload["questions"] = [
        _question(
            question_type="short_answer",
            material_refs=["M01"],
            stem_blocks=[_block("Q001-PROMPT", 1, "结合虚构材料说明法律问题。")],
            options=[],
            answer=None,
            answer_status="not_provided",
            answer_reason="样例未附参考答案",
        )
    ]
    payload["questions"][0]["answer_requirements"] = [
        {
            "order": index,
            "text": text,
            "locator": "PDF第1页",
            "provenance": "source_text",
        }
        for index, text in enumerate(
            ["不得照搬材料。", "观点正确且表达完整。", "不少于600字。"], 1
        )
    ]

    result = validate_structured_material(payload)
    preview = build_structured_material_preview(payload)

    assert result.fatal is False
    assert result.questions[0]["subquestions"] == []
    assert len(result.questions[0]["answer_requirements"]) == 3
    assert preview["counts"]["subquestions"] == 0
    assert preview["counts"]["answer_requirements"] == 3
    assert preview["questions"][0]["answer_requirement_count"] == 3


def test_answer_requirements_and_subquestion_answers_have_separate_counts():
    payload = _valid_document()
    question = _question(
        question_type="case_analysis",
        material_refs=["M01"],
        options=[],
        answer_status="provided",
        answer_reason="参考答案分别按原文编号关联至小问。",
        subquestions=[
            {
                "id": f"Q001-SUB-{index}",
                "source_number": str(index),
                "stem_blocks": [
                    _block(
                        f"Q001-SUB-{index}-STEM",
                        1,
                        f"虚构的独立问题{index}？",
                    )
                ],
                "answer": {
                    "provenance": "source_text",
                    "blocks": [
                        _block(
                            f"Q001-SUB-{index}-ANSWER",
                            1,
                            f"虚构参考答案{index}。",
                        )
                    ],
                },
                "answer_reason": "",
                "locators": ["PDF第1页"],
                "answer_requirements": [
                    {
                        "order": 1,
                        "text": "说明理由。",
                        "locator": "PDF第1页",
                        "provenance": "source_text",
                    }
                ],
            }
            for index in range(1, 4)
        ],
    )
    question["stem_blocks"] = []
    question["answer"] = None
    payload["questions"] = [question]

    preview = build_structured_material_preview(payload)

    assert preview["counts"]["subquestions"] == 3
    assert preview["counts"]["answer_requirements"] == 3
    assert preview["counts"]["answer_mappings_resolved"] == 3
    assert preview["counts"]["answer_mappings_unresolved"] == 0
    assert preview["questions"][0]["subquestion_count"] == 3


def test_material_stem_duplication_and_section_contamination_are_reviewed():
    payload = _valid_document()
    repeated = "仅供测试的虚构共同案情。" * 12
    payload["materials"][0]["blocks"] = [_block("M01-B01", 1, repeated)]
    payload["questions"][0]["material_refs"] = ["M01"]
    payload["questions"][0]["stem_blocks"] = [
        _block("Q001-S01", 1, repeated + "请分析该虚构争议。")
    ]
    payload["materials"][0]["blocks"][0]["text"] += "[参考答案]不应属于公共材料。"

    preview = build_structured_material_preview(payload)
    codes = {issue["code"] for issue in preview["issues"]}

    assert "question.material_stem_duplicate" in codes
    assert "material.section_contamination" in codes
    assert preview["counts"]["duplicate_warnings"] == 1
    assert preview["counts"]["contamination_warnings"] == 1


def test_malformed_stem_or_material_refs_report_errors_without_crashing_duplicate_check():
    payload = _valid_document()
    payload["questions"][0]["stem_blocks"] = "not-a-block-list"
    payload["questions"][0]["material_refs"] = {"not": "a-list"}

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert "block.type" in _issue_codes(result)
    assert "question.material_refs_type" in _issue_codes(result)


def test_requirement_like_subquestion_is_flagged_without_rejecting_document():
    payload = _valid_document()
    payload["questions"][0]["subquestions"] = [
        {
            "id": "Q001-SUB-01",
            "source_number": "1",
            "stem_blocks": [
                _block(
                    "Q001-SUB-01-B01",
                    1,
                    "总字数不得少于200字；观点应正确，表达完整，照搬材料不计分。",
                )
            ],
            "answer": None,
            "answer_reason": "虚构测试条款，不是小问答案",
            "locators": ["PDF第1页"],
        }
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert "subquestion.possible_answer_requirement" in _issue_codes(result)


def test_grouped_answer_criteria_are_reviewed_as_requirements_not_true_questions():
    payload = _valid_document()
    requirement_texts = [
        "无观点或论述、直接照搬材料原文的不得分。",
        "观点正确，表达完整、准确。",
        "总字数不少于600字。",
    ]
    payload["questions"][0]["subquestions"] = [
        {
            "id": f"Q001-SUB-{index}",
            "source_number": str(index),
            "stem_blocks": [
                _block(
                    f"Q001-SUB-{index}-STEM",
                    1,
                    text,
                )
            ],
            "answer": None,
            "answer_reason": "虚构验收条目不是小问答案",
            "locators": ["PDF第1页"],
        }
        for index, text in enumerate(requirement_texts, 1)
    ]

    result = validate_structured_material(payload)
    warnings = [
        issue
        for issue in result.issues
        if issue.code == "subquestion.possible_answer_requirement"
    ]

    assert len(warnings) == 3


def test_malformed_answer_requirement_is_an_entry_error():
    payload = _valid_document()
    payload["questions"][0]["answer_requirements"] = [
        {
            "order": 0,
            "text": "",
            "locator": "PDF第1页",
            "provenance": "source_text",
        }
    ]

    result = validate_structured_material(payload)

    assert result.usable_questions == 0
    assert "answer_requirement.order_invalid" in _issue_codes(result)
    assert "answer_requirement.text_missing" in _issue_codes(result)


def test_pdf_page_locator_is_metadata_and_is_not_removed_or_treated_as_stem_text():
    payload = _valid_document()
    payload["materials"][0]["blocks"][0]["locator"] = "PDF第1页"
    payload["questions"][0]["stem_blocks"] = [
        _block("Q001-S01", 1, "虚构题干不含来源页码。", locator="PDF第1页")
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.materials[0]["blocks"][0]["locator"] == "PDF第1页"
    assert result.questions[0]["stem_blocks"][0]["text"] == "虚构题干不含来源页码。"
    assert not any(
        issue.code == "material.section_contamination" for issue in result.issues
    )


def test_numbered_answers_map_to_the_matching_independent_case_questions():
    question_numbers = [1, 2, 3]
    question_stems = [
        "根据虚构事实一，第一项应如何判断？",
        "根据虚构事实二，第二项应如何判断？",
        "根据虚构事实三，第三项应如何判断？",
    ]
    answer_entries = [
        {"number": 1, "text": "根据虚构事实一，第一项应如何判断？答案：虚构答复一。"},
        {"number": 1, "text": "理由一：这是第一答案内部的虚构分点。"},
        {"number": 2, "text": "根据虚构事实二，第二项应如何判断？答案：虚构答复二。"},
        {"number": 2, "text": "理由二：这是第二答案内部的虚构分点。"},
        {"number": 3, "text": "根据虚构事实三，第三项应如何判断？答案：虚构答复三。"},
    ]

    mapping = match_numbered_answer_entries(
        question_numbers, question_stems, answer_entries
    )

    assert mapping == {0: 0, 1: 2, 2: 4}


def test_long_stem_split_into_ordered_blocks_is_not_an_error():
    payload = _valid_document()
    payload["questions"] = [
        _question(
            stem_blocks=[
                _block("Q001-S01", 1, "第一段" * 2000, locator="第1页"),
                _block("Q001-S02", 2, "第二段" * 2000, locator="第2页"),
            ],
        )
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_questions == 1
    assert not any(issue.code == "question.stem_too_long" for issue in result.issues)


def test_duplicate_ids_and_dangling_material_refs_reject_whole_document():
    duplicate = _valid_document()
    duplicate["questions"].append(_question("Q001", source_number="第2题"))
    result = validate_structured_material(duplicate)
    assert result.fatal is True
    assert "id.duplicate" in _issue_codes(result)

    dangling = _valid_document()
    dangling["questions"][0]["material_refs"] = ["M404"]
    result = validate_structured_material(dangling)
    assert result.fatal is True
    assert "question.material_ref_missing" in _issue_codes(result)


def test_invalid_option_answer_excludes_only_that_question():
    payload = _valid_document()
    payload["questions"] = [
        _question(
            "Q001",
            answer={"keys": ["Z"], "provenance": "source_text"},
        ),
        _question(
            "Q002",
            source_number="第2题",
            answer={"keys": ["A"], "provenance": "source_text"},
        ),
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_questions == 1
    assert "question.answer_key_unknown" in _issue_codes(result)
    assert any(item["id"] == "Q002" for item in result.valid_question_items)


def test_missing_answer_has_reason_and_is_reviewable_not_fatal():
    payload = _valid_document()
    payload["questions"] = [
        _question(
            answer=None,
            answer_status="not_provided",
            answer_reason="原始资料未提供参考答案",
        )
    ]
    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_questions == 1
    assert result.review_items == 1
    assert "question.answer_missing" in _issue_codes(result)


def test_missing_answer_reason_is_an_entry_error():
    payload = _valid_document()
    payload["questions"] = [_question(answer=None, answer_status="not_provided")]

    result = validate_structured_material(payload)

    assert result.usable_questions == 0
    assert "question.answer_reason_missing" in _issue_codes(result)


def test_external_model_answer_is_never_treated_as_source_answer():
    payload = _valid_document()
    payload["questions"] = [
        _question(
            answer={
                "keys": ["A"],
                "provenance": "external_model",
                "status": "supplemental",
            }
        )
    ]

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.questions[0]["answer"]["provenance"] == "external_model"
    assert result.review_items == 1
    assert "question.answer_not_source" in _issue_codes(result)


def test_suspected_merged_questions_are_pending_review():
    payload = _valid_document()
    payload["questions"] = [
        _question(
            stem_blocks=[
                _block(
                    "Q001-S01",
                    1,
                    "第1题 甲的行为如何评价？第2题 乙的行为如何评价？",
                )
            ]
        )
    ]

    result = validate_structured_material(payload)

    assert result.usable_questions == 1
    assert result.review_items == 1
    assert "question.suspected_merged" in _issue_codes(result)


def test_case_identity_claim_is_not_granted_by_template_fields():
    payload = _valid_document()
    payload["cases"][0]["authority"] = "最高人民法院"
    payload["cases"][0]["verification_status"] = "official_case"

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_cases == 1
    assert result.review_items == 1
    assert "case.protected_identity_claim" in _issue_codes(result)
    assert result.cases[0]["identity_granted"] is False


def test_non_string_verification_status_excludes_the_entry():
    payload = _valid_document()
    payload["questions"][0]["verification_status"] = 123

    result = validate_structured_material(payload)

    assert result.fatal is False
    assert result.usable_questions == 0
    assert "question.verification_status_type" in _issue_codes(result)


def test_block_order_and_source_metadata_require_manual_review():
    payload = _valid_document()
    payload["materials"][0]["blocks"] = [
        _block("M01-B01", 2, "第一块", locator=""),
        _block("M01-B02", 2, "第二块", provenance=""),
    ]
    result = validate_structured_material(payload)

    assert result.fatal is False
    assert "block.order_duplicate" in _issue_codes(result)
    assert "block.locator_missing" in _issue_codes(result)
    assert "block.provenance_missing" in _issue_codes(result)


def test_malformed_json_wrong_types_and_deep_payload_are_controlled_fatal_errors():
    malformed = validate_structured_material("{not-json")
    assert malformed.fatal is True
    assert "json.invalid" in _issue_codes(malformed)

    wrong_type = _valid_document()
    wrong_type["questions"] = {}
    result = validate_structured_material(wrong_type)
    assert result.fatal is True
    assert "root.questions_type" in _issue_codes(result)

    deep: list = []
    current = deep
    for _ in range(MAX_NESTING_DEPTH + 3):
        current.append([])
        current = current[0]
    too_deep = _valid_document()
    too_deep["document"]["extra"] = deep
    result = validate_structured_material(too_deep)
    assert result.fatal is True
    assert "json.too_deep" in _issue_codes(result)


def test_oversized_json_is_rejected_before_processing():
    payload = _valid_document()
    payload["document"]["source_description"] = "x" * (MAX_TOTAL_TEXT_CHARS + 1)

    result = validate_structured_material(payload)

    assert result.fatal is True
    assert "json.too_large" in _issue_codes(result)


def test_preview_reports_every_issue_with_stable_paths():
    payload = _valid_document()
    payload["questions"][0]["stem_blocks"][0]["text"] = ""

    preview = build_structured_material_preview(payload)

    assert preview["counts"]["entry_errors"] == 1
    assert any(
        issue["code"] == "question.stem_missing"
        and issue["path"] == "questions[0].stem_blocks"
        for issue in preview["issues"]
    )


def test_preview_keeps_entry_summary_when_one_question_has_an_entry_error():
    payload = _valid_document()
    payload["questions"].append(
        _question(
            "Q002",
            source_number="第2题",
            stem_blocks=[_block("Q002-S01", 1, "")],
        )
    )

    preview = build_structured_material_preview(payload)

    assert [item["id"] for item in preview["questions"]] == ["Q001", "Q002"]
    assert preview["counts"]["questions"] == 2
    assert preview["counts"]["processable"] == 2
    assert preview["counts"]["entry_errors"] == 1
