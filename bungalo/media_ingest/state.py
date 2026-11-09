"""
What the ingest remembers across restarts. Upload links are bearer credentials,
so only their hashes are ever written here.
"""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import NewType

from pydantic import BaseModel, Field

UploadSessionID = NewType("UploadSessionID", str)


class BatchVideo(BaseModel):
    number: int
    relative_path: str
    size: int
    fingerprint: str
    uploaded_to: list[str] = Field(default_factory=list)
    """Hashes of the Studio links this video has already reached."""

    upload_ids: dict[str, UploadSessionID] = Field(default_factory=dict)
    """Studio's id for an unfinished upload of this video, by link hash."""

    original_uploaded: bool = False
    """Whether Studio received this file itself, rather than only a transcode."""

    deleted: bool = False
    """Whether someone confirmed removing this video from its volume."""


class DeleteOffer(BaseModel):
    """The list of uploaded videos a `DELETE` reply would currently remove."""

    prompt_ts: str
    numbers: list[int]
    offered_at: datetime


class Batch(BaseModel):
    """One contact sheet post, open to comments in its Slack thread."""

    thread_ts: str
    volume_id: str
    volume_name: str
    created_at: datetime
    last_reply_ts: str
    videos: list[BatchVideo]
    delete_offer: DeleteOffer | None = None
    delete_offer_due: bool = False
    """Saved with each upload, so a crash can't lose the question that follows it."""


class IngestState(BaseModel):
    seen: dict[str, list[str]] = Field(default_factory=dict)
    """Fingerprints of the videos already announced, by volume id."""

    batches: list[Batch] = Field(default_factory=list)


def hash_link(link: str) -> str:
    """A stable identifier for a link that doesn't reveal it."""
    return hashlib.sha256(link.encode()).hexdigest()


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> IngestState:
        """The saved state, or an empty one on first run."""
        if not self.path.exists():
            return IngestState()
        return IngestState.model_validate_json(self.path.read_text())

    def save(self, state: IngestState) -> None:
        """Replace the file in one step, so a crash can't leave half of it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(indent=2))
        os.replace(temporary, self.path)
