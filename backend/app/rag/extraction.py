"""Validate uploaded files and extract their text, page by page where pages exist."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from ..core.config import SUPPORTED_EXTENSIONS

_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_BODY = "word/document.xml"
_DOCX_MAX_XML = 20 * 1024 * 1024  # uncompressed; a real policy is a few hundred KB


class ValidationError(Exception):
    """The upload is not acceptable. `status_code` is the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ExtractionError(Exception):
    """The file was accepted but no usable text could be extracted from it."""


@dataclass(frozen=True)
class Page:
    number: int | None  # 1-based page number; None for formats without pages
    text: str


def safe_filename(raw: str | None) -> str:
    """Display name only: files are stored on disk under their document ID, never this name."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if not name or name in {".", ".."}:
        raise ValidationError("The upload has no usable filename.")
    if len(name) > 200:
        stem, ext = Path(name).stem, Path(name).suffix
        name = stem[: 200 - len(ext)] + ext
    return name


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValidationError(
            f"Unsupported file type '{ext or 'none'}'. Supported types: {allowed}.", 415
        )
    return SUPPORTED_EXTENSIONS[ext]


def validate_content(file_type: str, data: bytes) -> None:
    """Cheap checks that the bytes match the claimed type, run before the upload is accepted."""
    if not data:
        raise ValidationError("The file is empty.")
    if file_type == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise ValidationError("The file has a .pdf extension but is not a valid PDF.")
    elif file_type == "docx":
        if not data.startswith(b"PK\x03\x04"):
            raise ValidationError("The file has a .docx extension but is not a Word document.")
    else:
        if b"\x00" in data[:8192]:
            raise ValidationError("The file looks like binary data, not text.")
        _decode_text(data)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("The text file is not UTF-8 or Windows-1252 encoded.")


def normalize_text(text: str) -> str:
    """Tidy whitespace without changing wording, so citations still match the source."""
    text = unicodedata.normalize("NFKC", text).replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join words hyphenated across lines
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pages(file_type: str, data: bytes) -> list[Page]:
    """Return the non-empty pages of a document. Raises ExtractionError if there is no text."""
    if file_type == "pdf":
        pages = _extract_pdf(data)
    elif file_type == "docx":
        pages = [Page(None, normalize_text(_extract_docx(data)))]
    else:
        pages = [Page(None, normalize_text(_decode_text(data)))]
    pages = [p for p in pages if p.text]
    if not pages:
        hint = " It may be a scanned PDF; OCR is not supported." if file_type == "pdf" else ""
        raise ExtractionError(f"No extractable text was found in the document.{hint}")
    return pages


def _unwrap_lines(text: str) -> str:
    """PDFs break every visual line. Re-join a line with the next one when the next clearly
    continues the sentence (starts lower-case), so chunks hold prose rather than fragments."""
    return re.sub(r"(?<=[^\n.!?:;])\n(?=[a-z])", " ", text)


def _extract_pdf(data: bytes) -> list[Page]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("The PDF is password-protected and cannot be read.")
        return [
            Page(i, _unwrap_lines(normalize_text(page.extract_text() or "")))
            for i, page in enumerate(reader.pages, start=1)
        ]
    except ExtractionError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError) as exc:
        raise ExtractionError(f"The PDF could not be parsed ({type(exc).__name__}).") from exc


def _extract_docx(data: bytes) -> str:
    """The text of a Word document in reading order. Each paragraph and each table row becomes
    its own block, so chunks split between them; a row's cells are joined with " | " so a row
    reads as one self-contained line ("Stage 2 | Supportive | Offers a hardship plan")."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(_DOCX_BODY)
            if info.file_size > _DOCX_MAX_XML:
                raise ExtractionError("The Word document is too large to read.")
            xml = archive.read(info)
    except KeyError as exc:
        raise ExtractionError("The file is not a Word document (it has no document body).") from exc
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError) as exc:
        raise ExtractionError(
            f"The Word document could not be opened ({type(exc).__name__})."
        ) from exc
    # Word never writes a DTD; refusing one rules out entity-expansion tricks.
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ExtractionError("The Word document contains unsupported XML.")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ExtractionError("The Word document could not be parsed.") from exc
    body = root.find(f"{_WORD}body")
    if body is None:
        return ""
    blocks = []
    for element in body:
        if element.tag == f"{_WORD}p":
            blocks.append(_docx_paragraph(element))
        elif element.tag == f"{_WORD}tbl":
            for row in element.iter(f"{_WORD}tr"):
                # Paragraphs inside one cell (a template's subject, greeting, body) stay lines.
                cells = [
                    "\n".join(filter(None, (_docx_paragraph(p) for p in cell.iter(f"{_WORD}p"))))
                    for cell in row.iter(f"{_WORD}tc")
                ]
                blocks.append(" | ".join(c for c in cells if c))
    return "\n\n".join(b for b in blocks if b.strip())


def _docx_paragraph(paragraph: ElementTree.Element) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag == f"{_WORD}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_WORD}tab":
            parts.append(" ")
        elif node.tag in (f"{_WORD}br", f"{_WORD}cr"):
            parts.append("\n")
    return "".join(parts).strip()
