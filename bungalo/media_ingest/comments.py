"""Reads upload requests out of Slack comments on a contact sheet."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_SLACK_LINK = re.compile(r"<(https?://[^>|]+)(?:\|[^>]*)?>", re.IGNORECASE)
_BARE_LINK = re.compile(r"https?://\S+", re.IGNORECASE)
_SLACK_MARKUP = re.compile(r"<[^>]*>")
_ALL = re.compile(r"\ball\b", re.IGNORECASE)
_SELECTION = re.compile(r"\b(\d+)(?:\s*-\s*(\d+))?\b")
_UPLOAD_PATH = re.compile(r"/public-upload/[A-Za-z0-9_-]+/?")


class CommentError(ValueError):
    """The comment asked for an upload but can't be acted on; safe to show."""


@dataclass(frozen=True)
class UploadRequest:
    link: str
    numbers: list[int]


def _is_authorized(url: str, studio_url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    studio = urlsplit(studio_url)
    return (
        parts.scheme.lower() == studio.scheme.lower()
        and parts.netloc.lower() == studio.netloc.lower()
        and not parts.query
        and not parts.fragment
        and _UPLOAD_PATH.fullmatch(parts.path) is not None
    )


def _selected_numbers(text: str, video_count: int) -> list[int]:
    if _ALL.search(text):
        return list(range(1, video_count + 1))
    chosen: set[int] = set()
    for match in _SELECTION.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end or start < 1 or end > video_count:
            raise CommentError(
                f"`{match.group(0)}` isn't in this contact sheet, "
                f"which is numbered 1 to {video_count}."
            )
        chosen.update(range(start, end + 1))
    return sorted(chosen)


def is_delete_confirmation(text: str) -> bool:
    """Whether a comment is exactly `DELETE`, the only reply that removes footage."""
    return text.strip() == "DELETE"


def parse_comment(
    text: str, *, studio_url: str, video_count: int
) -> UploadRequest | None:
    """
    An upload request from a comment, or None for ordinary conversation.

    :raises CommentError: when it selects videos without a usable Studio link
    """
    links = [match.group(1) for match in _SLACK_LINK.finditer(text)]
    remainder = _SLACK_MARKUP.sub(" ", text)
    links += _BARE_LINK.findall(remainder)
    remainder = _BARE_LINK.sub(" ", remainder)

    has_selection = bool(_ALL.search(remainder) or _SELECTION.search(remainder))
    if not links and not has_selection:
        return None

    example = f"{studio_url}/public-upload/KEY"
    authorized = {
        link.rstrip("/") for link in links if _is_authorized(link, studio_url)
    }
    if not authorized:
        raise CommentError(
            f"Uploads need a Studio public upload link, like `{example}`. "
            "Make one from the project's Footage page."
        )
    if len(authorized) > 1:
        raise CommentError("Use one Studio upload link per comment.")

    numbers = _selected_numbers(remainder, video_count)
    if not numbers:
        raise CommentError("Say which videos to upload, like `1 3 5-8` or `all`.")
    return UploadRequest(link=authorized.pop(), numbers=numbers)
