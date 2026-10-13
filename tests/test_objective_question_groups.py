from __future__ import annotations

import pytest

from objective_question_groups import (
    normalize_source_number,
    resolve_objective_question_group,
)


def _payload() -> dict:
    return {
        "schema_version": "1.1",
        "objective_question_groups": [
            {
                "id": "group-1",
                "material_refs": ["material-1"],
                "question_refs": ["question-a", "question-b", "question-c"],
                "publish_mode": "all_at_once",
            }
        ],
    }


def test_source_number_normalization_is_unicode_and_whitespace_stable():
    assert normalize_source_number("  第２０７　题 ") == "207"
    assert normalize_source_number("207") == "207"


@pytest.mark.parametrize("external_id", ["question-a", "question-b", "question-c"])
def test_explicit_member_lookup_returns_the_ordered_group(external_id: str):
    group = resolve_objective_question_group(_payload(), external_id)

    assert group is not None
    assert group["id"] == "group-1"
    assert group["question_refs"] == ["question-a", "question-b", "question-c"]


def test_unrelated_question_is_not_grouped_by_similarity_or_position():
    assert (
        resolve_objective_question_group(_payload(), "similar-looking-question") is None
    )


def test_corrupt_duplicate_membership_fails_closed():
    payload = _payload()
    payload["objective_question_groups"].append(
        {
            "id": "group-2",
            "material_refs": ["material-1"],
            "question_refs": ["question-b", "question-d"],
            "publish_mode": "all_at_once",
        }
    )

    with pytest.raises(ValueError, match="multiple objective groups"):
        resolve_objective_question_group(payload, "question-b")
