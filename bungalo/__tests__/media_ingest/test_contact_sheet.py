from io import BytesIO

from PIL import Image

from bungalo.media_ingest.contact_sheet import (
    ROWS_PER_SHEET,
    SheetRow,
    paginate,
    render_contact_sheet,
)


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (320, 180), (200, 10, 10)).save(output, format="JPEG")
    return output.getvalue()


def test_contact_sheet_survives_missing_and_corrupt_frames() -> None:
    rows = [
        SheetRow(1, "C0001.MP4", "1.0 GB  0:10", [_jpeg()] * 5),
        SheetRow(2, "x" * 500, "2.0 GB  1:00:00", [None, b"junk", _jpeg()]),
    ]
    sheet = Image.open(BytesIO(render_contact_sheet(rows)))
    assert sheet.width == 5 * 320 + 6 * 4
    assert sheet.height == 2 * (34 + 180 + 4) + 4


def test_paginate_splits_long_batches() -> None:
    rows = [SheetRow(i, "a", "b", []) for i in range(ROWS_PER_SHEET + 1)]
    assert [len(page) for page in paginate(rows)] == [ROWS_PER_SHEET, 1]
