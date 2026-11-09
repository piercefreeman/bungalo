"""Draws contact sheets: one labelled band of sampled frames for each video."""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from bungalo.media_ingest.box import FRAME_HEIGHT, FRAME_WIDTH, FRAMES_PER_VIDEO

ROWS_PER_SHEET = 10
GAP = 4
LABEL_HEIGHT = 34
BACKGROUND = (18, 18, 18)
PLACEHOLDER = (48, 48, 48)
TEXT = (240, 240, 240)
MUTED = (165, 165, 165)


@dataclass
class SheetRow:
    number: int
    name: str
    detail: str
    frames: list[bytes | None]


def paginate(rows: list[SheetRow]) -> list[list[SheetRow]]:
    """Split rows into groups small enough for one readable image."""
    return [
        rows[start : start + ROWS_PER_SHEET]
        for start in range(0, len(rows), ROWS_PER_SHEET)
    ]


def render_contact_sheet(rows: list[SheetRow]) -> bytes:
    """Render rows to a JPEG, leaving a grey cell for any unreadable frame."""
    width = FRAMES_PER_VIDEO * FRAME_WIDTH + (FRAMES_PER_VIDEO + 1) * GAP
    row_height = LABEL_HEIGHT + FRAME_HEIGHT + GAP
    sheet = Image.new("RGB", (width, row_height * len(rows) + GAP), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=20)

    for row_index, row in enumerate(rows):
        top = GAP + row_index * row_height
        number = f"{row.number}"
        draw.text((GAP + 4, top + 4), number, fill=TEXT, font=font)
        detail_width = draw.textlength(row.detail, font=font)
        draw.text(
            (width - GAP - 4 - detail_width, top + 4),
            row.detail,
            fill=MUTED,
            font=font,
        )
        name_left = GAP + 60
        room = width - name_left - detail_width - 40
        draw.text(
            (name_left, top + 4), _fit(draw, row.name, font, room), fill=TEXT, font=font
        )

        for frame_index in range(FRAMES_PER_VIDEO):
            frame = row.frames[frame_index] if frame_index < len(row.frames) else None
            left = GAP + frame_index * (FRAME_WIDTH + GAP)
            _paste_frame(sheet, draw, frame, left, top + LABEL_HEIGHT)

    output = BytesIO()
    sheet.save(output, format="JPEG", quality=82)
    return output.getvalue()


def _fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    room: float,
) -> str:
    if draw.textlength(text, font=font) <= room:
        return text
    while text and draw.textlength(f"{text}...", font=font) > room:
        text = text[:-1]
    return f"{text}..."


def _paste_frame(
    sheet: Image.Image,
    draw: ImageDraw.ImageDraw,
    frame: bytes | None,
    left: int,
    top: int,
) -> None:
    draw.rectangle(
        (left, top, left + FRAME_WIDTH - 1, top + FRAME_HEIGHT - 1), fill=PLACEHOLDER
    )
    if frame is None:
        return
    try:
        image = Image.open(BytesIO(frame)).convert("RGB")
    except (UnidentifiedImageError, OSError):
        return
    image.thumbnail((FRAME_WIDTH, FRAME_HEIGHT))
    sheet.paste(
        image,
        (
            left + (FRAME_WIDTH - image.width) // 2,
            top + (FRAME_HEIGHT - image.height) // 2,
        ),
    )
