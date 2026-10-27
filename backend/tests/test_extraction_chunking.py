import pytest

from app.rag.chunking import chunk_pages, split_text
from app.rag.extraction import (
    ExtractionError,
    Page,
    ValidationError,
    detect_file_type,
    extract_pages,
    normalize_text,
    safe_filename,
    validate_content,
)

from .helpers import make_pdf


def test_pdf_text_is_extracted_per_page():
    pages = extract_pages("pdf", make_pdf(["First page about fees.", "Second page about calls."]))
    assert [p.number for p in pages] == [1, 2]
    assert "fees" in pages[0].text and "calls" in pages[1].text


def test_pdf_wrapped_lines_are_rejoined_but_headings_stay_separate():
    text = (
        "3. Escalation\n"
        + "Every account must be escalated to a human officer before any notice. " * 3
    )
    (page,) = extract_pages("pdf", make_pdf([text]))
    assert page.text.startswith("3. Escalation\nEvery account must be escalated")
    assert "Every account must be escalated to a human officer before any notice." in page.text
    assert page.text.count("\n") == 1


def test_text_file_has_no_page_numbers():
    pages = extract_pages("txt", b"Line one.\r\n\r\n\r\n\r\nLine   two.")
    assert pages == [Page(None, "Line one.\n\nLine two.")]


def test_document_without_text_fails_extraction():
    with pytest.raises(ExtractionError, match="No extractable text"):
        extract_pages("pdf", make_pdf(["", ""]))
    with pytest.raises(ExtractionError):
        extract_pages("txt", b"   \n\n  ")


def test_corrupt_pdf_fails_extraction_cleanly():
    with pytest.raises(ExtractionError):
        extract_pages("pdf", b"%PDF-1.4\nthis is not really a pdf")


def test_normalize_rejoins_hyphenated_words():
    assert normalize_text("the cus-\ntomer  must\tpay") == "the customer must pay"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("policy.pdf", "policy.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\policy.PDF", "policy.PDF"),
        ("bad\x00name\n.txt", "badname.txt"),
    ],
)
def test_safe_filename_strips_paths_and_control_characters(raw, expected):
    assert safe_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "..", "dir/"])
def test_safe_filename_rejects_empty_names(raw):
    with pytest.raises(ValidationError):
        safe_filename(raw)


def test_unsupported_extension_is_rejected():
    assert detect_file_type("Policy.PDF") == "pdf"
    with pytest.raises(ValidationError) as exc:
        detect_file_type("malware.exe")
    assert exc.value.status_code == 415


def test_content_must_match_the_claimed_type():
    with pytest.raises(ValidationError, match="not a valid PDF"):
        validate_content("pdf", b"just some text")
    with pytest.raises(ValidationError, match="binary"):
        validate_content("txt", b"\x00\x01\x02binary")
    with pytest.raises(ValidationError, match="empty"):
        validate_content("txt", b"")
    validate_content("txt", "caf\u00e9".encode())


def test_chunks_respect_size_and_overlap():
    sentences = [f"Sentence number {i} explains rule {i} in detail." for i in range(60)]
    text = " ".join(sentences)
    chunks = split_text(text, size=300, overlap=60)
    assert len(chunks) > 5
    assert all(len(c) <= 300 for c in chunks)
    # Consecutive chunks share text, and nothing from the document is lost.
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a[-20:] in b or b[:20] in a
    for sentence in sentences:
        assert any(sentence in c for c in chunks)


def test_short_text_is_one_chunk_and_long_words_are_split():
    assert split_text("Short policy.", 300, 60) == ["Short policy."]
    chunks = split_text("x" * 1000, 300, 60)
    assert all(len(c) <= 300 for c in chunks) and "".join(chunks).count("x") >= 1000


def test_chunks_keep_page_number_and_running_index():
    pages = [Page(1, "Alpha. " * 100), Page(2, "Beta. " * 100)]
    chunks = chunk_pages(pages, size=200, overlap=40)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert {c.page_number for c in chunks} == {1, 2}
    assert all(("Alpha" in c.text) == (c.page_number == 1) for c in chunks)


def _docx(body: str) -> bytes:
    """A minimal Word file: just the parts a reader needs."""
    import io
    import zipfile

    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def _docx_with_doctype() -> bytes:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<!DOCTYPE x [<!ENTITY a "aaaa">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>",
        )
    return out.getvalue()


def test_word_documents_keep_paragraphs_and_table_rows_in_reading_order():
    body = (
        "<w:p><w:r><w:t>Customer Messaging Policy</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>An SMS has at most </w:t></w:r><w:r><w:t>300 characters.</w:t></w:r></w:p>"
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>Stage</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Tone</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>Stage 2</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Supportive</w:t></w:r>"
        "<w:r><w:tab/><w:t>and kind</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
        "<w:p><w:r><w:t>After the table.</w:t></w:r></w:p>"
    )
    data = _docx(body)
    validate_content("docx", data)
    [page] = extract_pages("docx", data)
    assert page.number is None
    assert page.text == (
        "Customer Messaging Policy\n\nAn SMS has at most 300 characters.\n\n"
        "Stage | Tone\n\nStage 2 | Supportive and kind\n\nAfter the table."
    )


def test_broken_or_hostile_word_files_are_refused():
    with pytest.raises(ValidationError):
        validate_content("docx", b"just some text")
    with pytest.raises(ExtractionError, match="could not be opened"):
        extract_pages("docx", b"PK\x03\x04 not really a zip")
    import io
    import zipfile

    no_body = io.BytesIO()
    with zipfile.ZipFile(no_body, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(ExtractionError, match="no document body"):
        extract_pages("docx", no_body.getvalue())
    # An entity declaration is how "billion laughs" XML bombs start; Word never writes one.
    with pytest.raises(ExtractionError, match="unsupported XML"):
        extract_pages("docx", _docx_with_doctype())
    with pytest.raises(ExtractionError, match="No extractable text"):
        extract_pages("docx", _docx("<w:p/>"))


def test_the_sample_messaging_policy_is_readable():
    from pathlib import Path

    data = (
        Path(__file__).resolve().parents[2] / "sample_policies" / "messaging_policy.docx"
    ).read_bytes()
    validate_content("docx", data)
    text = extract_pages("docx", data)[0].text
    assert "Policy ID | MG-04" in text
    assert (
        "Stage 2: 31 to 60 days overdue | Collections message tone in Stage 2: supportive" in text
    )
    assert "Subject: ComplyAgent Finance: about your account\nDear {first_name}," in text
    assert "SMS | A collections SMS is at most 300 characters long." in text
