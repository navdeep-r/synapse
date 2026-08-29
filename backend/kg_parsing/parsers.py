"""Format-specific text extraction.

R8: this layer is explicitly NOT hardened against hostile input. Decompression
bombs, macro-laden DOCX and malformed-PDF parser exploits are out of scope for
this project; only size caps, extension checks and parse-failure quarantine are
implemented. Do not point this at untrusted uploads.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".md", ".log", ".csv", ".json", ".pdf", ".docx", ".html", ".htm", ".css", ".scss", ".vue", ".svelte"}

_WHITESPACE_RUN = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class ParseError(RuntimeError):
    """Raised when a document cannot be turned into text."""


def normalize_text(raw: str) -> str:
    """Collapse whitespace without destroying paragraph structure."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
    text = _WHITESPACE_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _parse_pdf_pymupdf(data: bytes) -> str:
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        return "\n\n".join(page.get_text() for page in document)


def _parse_pdf_pypdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # a single bad page should not kill the document
            raise ParseError(f"PDF page extraction failed: {exc}") from exc
    return "\n\n".join(pages)


def _parse_pdf(data: bytes) -> str:
    """Extract with PyMuPDF, falling back to pypdf.

    Design-tool PDFs (Canva, Figma, InDesign) commonly position every glyph
    individually and emit real space characters between them, which pypdf returns
    verbatim as `P r o g r a m m i n g`. That is not a tunable — `space_width` has
    no effect because the spaces are in the content stream rather than inferred.
    PyMuPDF reconstructs words from glyph geometry and reads these correctly.
    """
    try:
        text = _parse_pdf_pymupdf(data)
    except ImportError:
        return _parse_pdf_pypdf(data)
    except Exception as exc:
        raise ParseError(f"PDF extraction failed: {exc}") from exc

    # A PDF whose text lives in an unsupported encoding can yield nothing here
    # while pypdf still recovers something, so keep it as a second opinion.
    return text if text.strip() else _parse_pdf_pypdf(data)


def _parse_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _parse_html(data: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n")


def parse_document(filename: str, data: bytes) -> tuple[str, str]:
    """Return `(normalized_text, media_type)` for an uploaded document."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ParseError(
            f"Unsupported file type '{suffix or filename}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    try:
        if suffix == ".pdf":
            text, media_type = _parse_pdf(data), "application/pdf"
        elif suffix == ".docx":
            text, media_type = (
                _parse_docx(data),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        elif suffix in {".html", ".htm"}:
            text, media_type = _parse_html(data), "text/html"
        else:
            text, media_type = data.decode("utf-8", errors="replace"), "text/plain"
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse {filename}: {exc}") from exc

    normalized = normalize_text(text)
    if not normalized:
        raise ParseError(f"{filename} produced no extractable text")
    return normalized, media_type
