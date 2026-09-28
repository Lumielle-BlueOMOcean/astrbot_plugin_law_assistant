import pytest

from pagination import page_payload, page_request


@pytest.mark.parametrize("size", [20, 50, 100])
def test_page_request_is_bounded_and_uses_one_based_pages(size):
    assert page_request("3", str(size)) == (3, size, (3 - 1) * size)


@pytest.mark.parametrize("page,size", [("bad", 50), (1, 17), (1, 1000)])
def test_page_request_rejects_invalid_page_inputs(page, size):
    with pytest.raises(ValueError):
        page_request(page, size)


def test_page_request_clamps_nonpositive_page_to_first_page():
    assert page_request(0, 20) == (1, 20, 0)


def test_page_payload_has_stable_empty_and_last_page_metadata():
    assert page_payload(["x"], page=2, page_size=20, total=21) == {
        "items": ["x"],
        "page": 2,
        "page_size": 20,
        "total": 21,
        "page_count": 2,
    }
    assert page_payload([], page=1, page_size=20, total=0)["page_count"] == 0
