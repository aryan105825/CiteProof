# @exports: parse_item(item: dict) -> Candidate | None
# @exports: search(raw_ref: str, cfg: Config, rows: int = 5) -> tuple[list[Candidate], int]
# @exports: lookup_doi(doi: str, cfg: Config) -> tuple[Candidate | None, int]
# @exports: sample_records(n: int, cfg: Config, filt: str = "type:journal-article,from-pub-date:2000-01-01,until-pub-date:2025-12-31") -> list[Candidate]
# @imports: config.py:Config
# @imports: httpclient.py:get_json, HttpError
# @imports: schemas.py:Candidate
# @env: none
# @schema: search GET /works?query.bibliographic=<raw[:600]>&rows=&select=DOI,title,author,issued,container-title,score,URL,volume,page&mailto=
# @schema: lookup_doi GET /works/{doi}; 404 -> None; returned Candidate has index_score=None
# @schema: sample_records requires cfg.use_cache False (sampling is random); keeps only records with title+authors+year+venue; raises RuntimeError if n cannot be reached
import dataclasses
import html
import re
from urllib.parse import quote

from citeproof.config import Config
from citeproof.httpclient import HttpError, get_json
from citeproof.schemas import Candidate

_SELECT = "DOI,title,author,issued,container-title,score,URL,volume,page"
_TAGS = re.compile(r"<[^>]+>")


def _first_str(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(html.unescape(_TAGS.sub("", value)).split())
    return cleaned or None


def parse_item(item: dict) -> Candidate | None:
    title = _clean_text(_first_str(item.get("title")))
    if title is None:
        return None
    authors: list[str] = []
    for a in item.get("author") or []:
        family = a.get("family") if isinstance(a, dict) else None
        if isinstance(family, str) and family.strip():
            authors.append(family.strip())
        if len(authors) == 20:
            break
    year = None
    parts = (item.get("issued") or {}).get("date-parts") or []
    if parts and isinstance(parts[0], list) and parts[0]:
        first = parts[0][0]
        if isinstance(first, int) and not isinstance(first, bool):
            year = first
    doi = item.get("DOI")
    doi = doi.strip().lower() if isinstance(doi, str) and doi.strip() else None
    score = item.get("score")
    index_score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    return Candidate(
        source="crossref",
        doi=doi,
        title=title,
        authors=authors,
        year=year,
        venue=_clean_text(_first_str(item.get("container-title"))),
        volume=_first_str(item.get("volume")),
        pages=_first_str(item.get("page")),
        url=_first_str(item.get("URL")),
        index_score=index_score,
    )


def search(raw_ref: str, cfg: Config, rows: int = 5) -> tuple[list[Candidate], int]:
    query = " ".join(raw_ref.split())[:600]
    if query == "":
        raise ValueError("crossref.search was given an empty reference string. Pass the reference text.")
    params = {"query.bibliographic": query, "rows": str(rows), "select": _SELECT, "mailto": cfg.mailto}
    data, made = get_json(f"{cfg.crossref_base}/works", params, cfg)
    if data is None:
        raise HttpError(f"Crossref search returned HTTP 404 for {cfg.crossref_base}/works. Check that crossref_base is correct.")
    items = (data.get("message") or {}).get("items") or []
    cands = [c for c in (parse_item(i) for i in items) if c is not None]
    return cands, made


def lookup_doi(doi: str, cfg: Config) -> tuple[Candidate | None, int]:
    url = f"{cfg.crossref_base}/works/{quote(doi, safe='/')}"
    data, made = get_json(url, {"mailto": cfg.mailto}, cfg)
    if data is None:
        return None, made
    cand = parse_item(data.get("message") or {})
    if cand is None:
        return None, made
    return dataclasses.replace(cand, index_score=None), made


def sample_records(
    n: int,
    cfg: Config,
    filt: str = "type:journal-article,from-pub-date:2000-01-01,until-pub-date:2025-12-31",
) -> list[Candidate]:
    if n <= 0:
        raise ValueError(f"sample_records needs n > 0, got {n}.")
    if cfg.use_cache:
        raise ValueError(
            "sample_records must run with the cache off, because Crossref samples are random and a cache would "
            "replay the same page. Use load_config(use_cache=False) or set CITEPROOF_CACHE=0."
        )
    out: list[Candidate] = []
    seen: set[str] = set()
    max_pages = n // 20 + 10
    pages = 0
    while len(out) < n and pages < max_pages:
        pages += 1
        params = {"sample": "100", "filter": filt, "select": _SELECT, "mailto": cfg.mailto}
        data, _ = get_json(f"{cfg.crossref_base}/works", params, cfg)
        if data is None:
            raise HttpError(f"Crossref sample returned HTTP 404 for {cfg.crossref_base}/works. Check the filter {filt!r}.")
        for item in (data.get("message") or {}).get("items") or []:
            cand = parse_item(item)
            if cand is None or not cand.authors or cand.year is None or not cand.venue:
                continue
            key = cand.doi or f"{cand.title}|{cand.year}"
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) == n:
                break
    if len(out) < n:
        raise RuntimeError(
            f"Collected only {len(out)} of {n} usable Crossref records after {pages} pages. "
            "Loosen the filter or lower n."
        )
    return out
