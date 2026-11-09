import pytest

from bungalo.media_ingest.comments import (
    CommentError,
    is_delete_confirmation,
    parse_comment,
)

STUDIO = "https://studio.example.com"


LINK = f"{STUDIO}/public-upload/abc_DEF-123"


def parse(text: str, count: int = 10):
    return parse_comment(text, studio_url=STUDIO, video_count=count)


def test_comment_reads_numbers_ranges_and_slack_wrapped_link() -> None:
    request = parse(f"<@U123> upload 1, 3 5-7 <{LINK}|studio link>")
    assert request is not None
    assert request.link == LINK
    assert request.numbers == [1, 3, 5, 6, 7]


def test_comment_all_selects_everything() -> None:
    request = parse(f"all <{LINK}>", count=3)
    assert request is not None and request.numbers == [1, 2, 3]


def test_comment_digits_inside_the_link_are_not_a_selection() -> None:
    with pytest.raises(CommentError, match="Say which"):
        parse(f"<{LINK}>")


def test_ordinary_conversation_is_ignored() -> None:
    assert parse("love these shots") is None


def test_selection_without_link_is_refused() -> None:
    with pytest.raises(CommentError, match="Studio public upload link"):
        parse("upload 1 2")


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example.com/public-upload/abc",
        "http://studio.example.com/public-upload/abc",
        f"{STUDIO}/projects/abc",
        f"{STUDIO}/public-upload/abc?x=1",
        f"{STUDIO}/public-upload/a%22b",
    ],
)
def test_unauthorized_links_are_refused(link: str) -> None:
    with pytest.raises(CommentError, match="Studio public upload link"):
        parse(f"1 <{link}>")


def test_two_different_links_are_refused() -> None:
    with pytest.raises(CommentError, match="one Studio upload link"):
        parse(f"1 <{LINK}> <{LINK}2>")


def test_out_of_range_selection_is_refused() -> None:
    with pytest.raises(CommentError, match="numbered 1 to 3"):
        parse(f"2-9 <{LINK}>", count=3)


@pytest.mark.parametrize(
    ("text", "confirmed"),
    [
        ("DELETE", True),
        ("  DELETE\n", True),
        ("delete", False),
        ("Delete", False),
        ("DELETE 1", False),
        ("please DELETE these", False),
    ],
)
def test_only_an_exact_delete_confirms(text: str, confirmed: bool) -> None:
    assert is_delete_confirmation(text) is confirmed
