from __future__ import annotations

import pytest

from content import (
    QUESTION_TYPE_LABELS,
    QuestionRequest,
    RealQuestion,
    normalize_question_type,
    real_question_identity_key,
    validate_generated_question,
)


def _real_question(**updates):
    record = {
        "source_name": "合成授权题库",
        "exam_name": "合成法律考试",
        "exam_year": "2023",
        "exam_date": "2023-09-01",
        "paper": "A卷",
        "question_number": "第1题",
        "source_locator": "第一部分第1题",
        "source_url": "https://example.test/exam/1",
        "subject": "刑法",
        "question_type": "不定项选择题",
        "stem": "以下说法哪些成立？",
        "options": ["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
        "answer": ["B"],
        "answer_source": "official",
        "verification_status": "verified",
    }
    record.update(updates)
    return record


@pytest.mark.parametrize("answer", [["B"], ["A", "C"]])
def test_indefinite_choice_accepts_one_or_multiple_valid_answers(answer):
    question = RealQuestion.from_mapping(_real_question(answer=answer))

    assert question.question_type == "indefinite_choice"
    assert question.to_mapping()["answer"] == answer
    assert question.to_mapping()["answer_source"] == "official"
    assert QUESTION_TYPE_LABELS[question.question_type] == "不定项选择题"


def test_indefinite_choice_rejects_unknown_answer_key():
    with pytest.raises(ValueError, match="answer.*option|选项"):
        RealQuestion.from_mapping(_real_question(answer=["E"]))


def test_single_choice_still_rejects_multiple_answer_keys():
    with pytest.raises(ValueError, match="single_choice.*one|单选"):
        RealQuestion.from_mapping(
            _real_question(question_type="single_choice", answer=["A", "B"])
        )


def test_indefinite_choice_aliases_flow_through_requests_and_generation_validation():
    assert normalize_question_type("不定项") == "indefinite_choice"
    assert normalize_question_type("不定项选择") == "indefinite_choice"
    assert normalize_question_type("indefinite") == "indefinite_choice"
    assert normalize_question_type("indefinite_choice") == "indefinite_choice"
    assert normalize_question_type("任") is None
    assert (
        QuestionRequest.from_values(question_type="不定项选择题").question_type
        == "indefinite_choice"
    )
    assert validate_generated_question(
        {
            "question": "哪些成立？",
            "options": ["A. 甲", "B. 乙"],
            "answer": ["A", "B"],
            "explanation": "合成解析。",
        },
        "indefinite_choice",
    )
    assert not validate_generated_question(
        {
            "question": "哪些成立？",
            "options": ["A. 甲", "B. 乙"],
            "answer": ["C"],
            "explanation": "合成解析。",
        },
        "indefinite_choice",
    )
    assert not validate_generated_question(
        {
            "question": "哪项成立？",
            "options": ["A. 甲", "B. 乙"],
            "answer": ["A", "B"],
            "explanation": "合成解析。",
        },
        "single_choice",
    )


@pytest.mark.parametrize(
    ("answer", "answer_source"),
    [
        (None, None),
        ("A", "official"),
        ("A", "third_party"),
        ("A", "user_verified"),
        ("A", "unverified"),
    ],
)
def test_real_question_answer_provenance_accepts_consistent_states(
    answer, answer_source
):
    record = _real_question(answer=answer)
    if answer_source is not None:
        record["answer_source"] = answer_source
    else:
        record.pop("answer_source")
    question = RealQuestion.from_mapping(record)

    expected = answer_source or "not_provided"
    assert question.answer_source == expected
    assert question.to_mapping()["answer_source"] == expected


@pytest.mark.parametrize(
    ("answer", "answer_source"),
    [
        ("A", None),
        ("A", ""),
        ("A", "not_provided"),
        (None, "official"),
        ("A", "invented"),
    ],
)
def test_real_question_answer_provenance_rejects_inconsistent_states(
    answer, answer_source
):
    record = _real_question(answer=answer)
    if answer_source is not None:
        record["answer_source"] = answer_source
    else:
        record.pop("answer_source")

    with pytest.raises(ValueError, match="answer_source"):
        RealQuestion.from_mapping(record)


def test_real_question_content_hash_includes_exam_year_date_and_paper():
    base = _real_question()
    original = RealQuestion.from_mapping(base)

    for field, value in (
        ("exam_year", "2022"),
        ("exam_date", "2023-09-02"),
        ("paper", "B卷"),
    ):
        changed = RealQuestion.from_mapping({**base, field: value})
        assert changed.content_hash != original.content_hash


def test_real_question_identity_is_deterministic_and_excludes_answer_content():
    original = RealQuestion.from_mapping(_real_question())
    changed = RealQuestion.from_mapping(
        _real_question(answer=["A"], explanation="修订后的合成解析。")
    )

    assert real_question_identity_key(original).startswith("rq:v2:")
    assert real_question_identity_key(original) == real_question_identity_key(changed)
    assert real_question_identity_key(original) != real_question_identity_key(
        RealQuestion.from_mapping(_real_question(source_locator="第一部分第2题"))
    )


def test_real_question_identity_uses_content_hash_only_when_location_is_missing():
    first = {
        "source_name": "合成题库",
        "exam_name": "合成考试",
        "content_hash": "content-a",
    }
    second = {**first, "content_hash": "content-b"}

    assert real_question_identity_key(first) != real_question_identity_key(second)


def test_real_question_identity_fallback_is_stable_when_answer_changes():
    original = RealQuestion.from_mapping(
        _real_question(
            question_number="",
            source_locator="",
            source_url="https://example.test/exam/without-locator",
        )
    )
    revised = RealQuestion.from_mapping(
        _real_question(
            question_number="",
            source_locator="",
            source_url="https://example.test/exam/without-locator",
            answer=["A", "C"],
            explanation="仅答案与解析发生修订。",
        )
    )

    assert original.content_hash != revised.content_hash
    assert real_question_identity_key(original) == real_question_identity_key(revised)
