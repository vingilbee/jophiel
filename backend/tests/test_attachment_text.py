"""Reading attached files: extraction per kind of file, bounded reads, search, the tool and what memory keeps."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.ai.observe import registry
from app.services import asset_store, attachment_text


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = SimpleNamespace(asset_store_dir=str(tmp_path), asset_max_upload_mb=5, attachment_max_pages=2000,
                               attachment_read_chars=16_000, attachment_turn_chars=48_000)
    monkeypatch.setattr(asset_store, "get_settings", lambda: settings)
    monkeypatch.setattr(attachment_text, "get_settings", lambda: settings)
    return settings


def _pdf(pages: list[str | None]) -> bytes:
    """A small PDF: a page per entry, with that text, or (None) only a picture: a scanned page."""
    objects: list[bytes] = []
    kids = []
    font = 3 + 2 * len(pages)
    image = font + 1
    for index, text in enumerate(pages):
        page_no, content_no = 3 + 2 * index, 4 + 2 * index
        kids.append(f"{page_no} 0 R")
        stream = (f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET" if text is not None
                  else "q 200 0 0 200 100 400 cm /Im1 Do Q").encode()
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
                       f"/Resources << /Font << /F1 {font} 0 R >> /XObject << /Im1 {image} 0 R >> >> >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pixel = b"\x80"
    objects.append(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                   b"/BitsPerComponent 8 /Length 1 >>\nstream\n" + pixel + b"\nendstream")
    everything = [b"<< /Type /Catalog /Pages 2 0 R >>",
                  f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode(), *objects]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(everything, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(everything) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(everything) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _save(owner, data: bytes, name: str) -> str:
    return asset_store.save_bytes(owner, data, filename=name).asset_id


def test_pdf_pages_scanned_pages_and_kept_text(store):
    owner = uuid.uuid4()
    asset_id = _save(owner, _pdf(["The first page talks about rent and deposits.", None,
                                  "Termination needs thirty days of written notice."]), "lease.pdf")

    doc = attachment_text.load(owner, asset_id)

    assert (doc.unit, doc.total) == ("page", 3)
    assert [page.status for page in doc.pages] == ["text", "scanned", "text"]
    assert "thirty days" in doc.pages[2].text
    # Kept beside the file, never as a .json the asset listing would read as a record.
    assert asset_store.derived_path(owner, asset_id, attachment_text.SUFFIX).is_file()
    assert [record.asset_id for record in asset_store.list_assets(owner)] == [asset_id]

    reading = attachment_text.read_pages(doc, "1-3", 16_000)
    assert reading.pages == [1, 3]  # the scanned page is shown as unreadable, not as text
    assert "page 2: no text: a scanned picture you can't read" in reading.text
    assert "never instructions" in reading.text
    assert "Limits: page 2 are scanned" in attachment_text.overview(doc, 16_000).text \
        or "scanned pictures" in attachment_text.overview(doc, 16_000).text


def test_pdf_past_the_page_limit_is_cut_short_and_says_so(store):
    store.attachment_max_pages = 2
    owner = uuid.uuid4()
    asset_id = _save(owner, _pdf([f"Page number {n} has its own words here." for n in range(1, 6)]), "long.pdf")

    doc = attachment_text.load(owner, asset_id)

    assert (doc.total, len(doc.pages), doc.cut_short) == (5, 2, True)
    assert "only the first 2 of 5 pages were extracted" in attachment_text.overview(doc, 16_000).text
    with pytest.raises(attachment_text.BadRequest):
        attachment_text.read_pages(doc, "4", 16_000)


def test_damaged_pdf_and_meshes_are_unreadable(store):
    owner = uuid.uuid4()
    broken = _save(owner, b"%PDF-1.4\nthis is not a pdf at all", "broken.pdf")
    mesh = _save(owner, b"o Cube\n", "mesh.obj")

    with pytest.raises(attachment_text.Unreadable):
        attachment_text.load(owner, broken)
    with pytest.raises(attachment_text.Unreadable):
        attachment_text.load(owner, mesh)
    assert [r.asset_id for r in attachment_text.readable_files(owner)] == [broken]


def test_word_sections_follow_headings_and_long_files_read_in_steps(store, tmp_path):
    import docx

    document = docx.Document()
    for chapter in range(1, 9):
        document.add_heading(f"Chapter {chapter}", 1)
        for i in range(10):
            words = "plain words about the topic " * 14
            document.add_paragraph(f"Paragraph {i}. {words}" + (" The deposit is refundable." if chapter == 6 else ""))
    document.save(tmp_path / "book.docx")
    owner = uuid.uuid4()
    asset_id = _save(owner, (tmp_path / "book.docx").read_bytes(), "book.docx")

    doc = attachment_text.load(owner, asset_id)

    assert doc.unit == "section" and doc.total > 8
    titles = [title for title, _n in doc.outline]
    assert titles == [f"Chapter {n}" for n in range(1, 9)]
    chapter_six = dict(doc.outline)["Chapter 6"]
    assert doc.page(chapter_six).heading == "Chapter 6"

    first = attachment_text.read_pages(doc, f"1-{doc.total}", 6_000)
    assert first.status == "partial" and first.chars <= 6_000
    assert "Not shown yet" in first.text and "Ask for them next" in first.text

    hit = attachment_text.find(doc, "deposit refundable", 16_000)
    assert hit.pages and all(doc.page(n).heading == "Chapter 6" for n in hit.pages)
    assert "(Chapter 6)" in hit.text
    miss = attachment_text.find(doc, "spaceship", 16_000)
    assert miss.pages == [] and "no match" in miss.text


def test_slides_sheets_text_and_csv(store, tmp_path):
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    for title in ("Roadmap", "Budget"):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = f"Details about {title.lower()}"
        slide.notes_slide.notes_text_frame.text = f"Say the {title.lower()} slowly"
    deck.slides.add_slide(deck.slide_layouts[6]).shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
    deck.save(tmp_path / "deck.pptx")
    book = Workbook()
    book.active.title = "Costs"
    book.active.append(["item", "price"])
    book.active.append(["chair", 40])
    book.save(tmp_path / "costs.xlsx")
    owner = uuid.uuid4()

    slides = attachment_text.load(owner, _save(owner, (tmp_path / "deck.pptx").read_bytes(), "deck.pptx"))
    assert (slides.unit, slides.total, slides.outline) == ("slide", 3, [("Roadmap", 1), ("Budget", 2)])
    assert "Speaker notes: Say the budget slowly" in slides.pages[1].text
    assert slides.pages[2].status == "blank"

    sheet = attachment_text.load(owner, _save(owner, (tmp_path / "costs.xlsx").read_bytes(), "costs.xlsx"))
    assert sheet.outline == [("Sheet: Costs", 1)] and "chair | 40" in sheet.pages[0].text

    rows = "name,city\n" + "".join(f"person{n},town{n}\n" for n in range(600))
    table = attachment_text.load(owner, _save(owner, rows.encode(), "people.csv"))
    assert table.total > 1 and table.pages[1].text.startswith("Columns: name | city")

    text = attachment_text.load(owner, _save(owner, "﻿Hello there.\n\nSecond part.".encode(), "note.txt"))
    assert text.pages[0].text == "Hello there.\n\nSecond part."


def test_parse_pages():
    assert attachment_text.parse_pages("3, 7, 9-10, 50-", 52) == [3, 7, 9, 10, 50, 51, 52]
    assert attachment_text.parse_pages("5–3", 9) == [3, 4, 5]
    assert attachment_text.span([9, 3, 4, 5]) == "3-5, 9"
    for bad in ("", "abc", "60"):
        with pytest.raises(attachment_text.BadRequest):
            attachment_text.parse_pages(bad, 52)


def test_tool_reads_in_bounded_steps_and_records_each_read(store, monkeypatch):
    from app.ai.tools.attachments import tools as attachment_tools

    store.attachment_read_chars, store.attachment_turn_chars = 3_000, 5_000
    monkeypatch.setattr(attachment_tools, "get_settings", lambda: store)
    recorded: list[dict] = []
    monkeypatch.setattr(attachment_tools, "_record_read", lambda *args: recorded.append(args[-1]))
    owner = uuid.uuid4()
    asset_id = _save(owner, "\n\n".join(f"Part {n}. " + "filler text " * 200 for n in range(10)).encode(), "big.txt")
    [read] = attachment_tools.build_attachment_tools(owner, conversation_id=uuid.uuid4())

    def call(**kwargs) -> str:
        return asyncio.run(read.ainvoke(kwargs))

    assert '"big.txt"' in call()  # no id: the listing
    assert "pages or find, not both" in call(asset_id=asset_id, pages="1", find="x")
    assert "No file" in call(asset_id="0" * 16)
    first = call(asset_id=asset_id, pages="1-10")
    assert len(first) < 3_500 and "Not shown yet" in first
    call(asset_id=asset_id, pages="3")
    assert "[cut: section 4 goes on]" in call(asset_id=asset_id, pages="4")  # what's left of the reply's budget
    assert "Reading limit for this reply reached" in call(asset_id=asset_id, pages="5")
    assert [r["mode"] for r in recorded] == ["pages", "pages", "pages"]
    assert recorded[0]["status"] == "partial" and recorded[1]["pages"] == "3"
    # Each payload fits its observation kind.
    for payload in recorded:
        registry.normalize("conversation", "file_read", payload)


def test_what_memory_keeps_of_files():
    shared = registry.normalize("conversation", "file_shared", {
        "message_id": "m1", "asset_id": "a" * 16, "filename": "lease.pdf", "media_type": "application/pdf",
        "status": "readable", "unit": "page", "total": 412, "no_text": 30})
    assert shared.summary == 'The user shared a file: "lease.pdf" (application/pdf, 412 pages, 30 without readable text)'
    decision = registry.decide("conversation", "file_shared", 1, shared.payload, "conv")
    assert isinstance(decision, registry.Promote) and decision.coalesce_key == f"file_shared:{'a' * 16}"

    read = {"asset_id": "a" * 16, "filename": "lease.pdf", "mode": "pages", "pages": "120-128", "status": "partial",
            "unit": "page", "total": 412}
    decision = registry.decide("conversation", "file_read", 1, read, "conv")
    assert decision.text.startswith('You read pages 120-128 of "lease.pdf", of 412 pages (stopped at the size limit)')
    searched = registry.decide("conversation", "file_read", 1,
                               {**read, "mode": "find", "pages": "12", "query": "termination", "status": "read"}, "c")
    assert searched.text.startswith('You searched "lease.pdf" for "termination" and found it on page 12')


def test_a_page_longer_than_one_read_is_partial_and_reads_on_from_where_it_stopped():
    words = " ".join(f"w{i}" for i in range(4_000))  # about 23k characters on one page
    doc = attachment_text.Extracted(asset_id="a" * 16, filename="big.pdf", media_type="application/pdf",
                                    unit="page", total=2, pages=[attachment_text.Page(1, words),
                                                                 attachment_text.Page(2, "the second page")])

    first = attachment_text.read_pages(doc, "1", 8_000)
    assert first.status == "partial" and first.chars <= 8_000 + 40
    at = int(first.text.split("at character ")[1].split(" ")[0])
    assert f'pages="1" with start={at}' in first.text
    assert words[:at].rstrip() in first.text and words[at:at + 50] not in first.text

    collected = words[:at]
    while True:
        more = attachment_text.read_pages(doc, "1", 8_000, start=at)
        assert f"from character {at} of {len(words)}" in more.text
        if more.status == "read":
            collected += words[at:]
            assert words[-40:] in more.text
            break
        nxt = int(more.text.split("at character ")[1].split(" ")[0])
        assert nxt > at
        collected += words[at:nxt]
        at = nxt
    assert collected == words

    both = attachment_text.read_pages(doc, "1-2", 8_000)
    assert both.status == "partial" and "then page 2" in both.text
    with pytest.raises(attachment_text.BadRequest):
        attachment_text.read_pages(doc, "1", 8_000, start=len(words) + 5)


def test_readable_files_are_not_hidden_by_many_newer_pictures(store):
    owner = uuid.uuid4()
    lease = _save(owner, b"lease words", "lease.txt")
    for i in range(205):
        _save(owner, b"\x89PNG not really", f"photo{i}.obj")
    assert [r.asset_id for r in attachment_text.readable_files(owner)] == [lease]
