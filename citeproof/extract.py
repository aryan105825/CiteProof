# @exports: read_input(path: str) -> str
# @exports: find_reference_block(text: str) -> str
# @exports: split_references(block: str) -> list[str]
# @exports: extract_references(text: str) -> list[str]
# @imports: none
# @env: none
# @schema: read_input supports .txt/.md (utf-8) and .pdf (pypdf, pages joined with "\n", single-column only); raises ValueError for other types, unreadable or text-less PDFs
# @schema: find_reference_block returns the text after the LAST heading References|Bibliography|Works Cited|Literature Cited, else the whole text
# @schema: split_references tries [n] / n. / n) markers (>=2 marker lines), then blank-line paragraphs (>=2), then reference-end lines (year, page range, DOI or URL); unwraps lines, de-hyphenates, strips markers, drops entries under 25 chars
import os
import re

from pypdf import PdfReader
from pypdf.errors import PyPdfError

_HEADING = re.compile(
    r"^[ \t]*#*[ \t]*(?:\d+[.)]?[ \t]+)?(?:references|bibliography|works cited|literature cited)[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_MARKER = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)])\s+")
_REF_END = re.compile(
    r"(?:(?:19|20)\d{2}[a-z]?\)?|\d+\s*[-\u2013\u2014]\s*\d+|10\.\d{4,9}/\S+|https?://\S+)[.)]?\s*$"
)
_MIN_LEN = 25


def read_input(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError as e:
            raise ValueError(f"{path} is not valid UTF-8 text ({e}). Re-save it as UTF-8 and try again.") from e
    if ext == ".pdf":
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError(f"{path} is encrypted. Remove the password and try again.")
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except PyPdfError as e:
            raise ValueError(f"{path} could not be read as a PDF ({e}). Check that the file is a valid PDF.") from e
        if text.strip() == "":
            raise ValueError(
                f"{path} contains no extractable text. Scanned PDFs need OCR first; paste the references as text instead."
            )
        return text
    raise ValueError(f"Unsupported file type {ext or '(none)'!r} for {path}. Use .txt, .md or .pdf.")


def find_reference_block(text: str) -> str:
    matches = list(_HEADING.finditer(text))
    if not matches:
        return text
    return text[matches[-1].end():].lstrip("\r\n")


def _join_lines(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if line == "":
            continue
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}" if out else line
    return " ".join(out.split())


def _split_by_markers(lines: list[str]) -> list[str]:
    refs: list[list[str]] = []
    for line in lines:
        if _MARKER.match(line):
            refs.append([line])
        elif refs:
            refs[-1].append(line)
    return [_join_lines(r) for r in refs]


def _split_by_ends(lines: list[str]) -> list[str]:
    refs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            continue
        current.append(line)
        if _REF_END.search(line.strip()):
            refs.append(_join_lines(current))
            current = []
    if current:
        refs.append(_join_lines(current))
    return refs


def split_references(block: str) -> list[str]:
    lines = block.splitlines()
    marker_lines = sum(1 for line in lines if _MARKER.match(line))
    paragraphs = [p for p in re.split(r"\n\s*\n", block) if p.strip()]
    if marker_lines >= 2:
        refs = _split_by_markers(lines)
    elif len(paragraphs) >= 2:
        refs = [_join_lines(p.splitlines()) for p in paragraphs]
    else:
        refs = _split_by_ends(lines)
    cleaned = [_MARKER.sub("", r, count=1).strip() for r in refs]
    return [r for r in cleaned if len(r) >= _MIN_LEN]


def extract_references(text: str) -> list[str]:
    return split_references(find_reference_block(text))
