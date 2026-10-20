# @exports: family_name(display_name: str) -> str | None
# @exports: parse_item(item: dict) -> Candidate | None
# @exports: search(raw_ref: str, cfg: Config, per_page: int = 5) -> tuple[list[Candidate], int]
# @exports: lookup_doi(doi: str, cfg: Config) -> tuple[Candidate | None, int]
# @imports: config.py:Config
# @imports: httpclient.py:get_json, HttpError
# @imports: schemas.py:Candidate
# @env: none
# @schema: search GET /works?search=<first 200 chars>&per_page=&select=id,doi,title,display_name,authorships,publication_year,primary_location,biblio,relevance_score&mailto=
# @schema: lookup_doi GET /works/https://doi.org/<doi>; 404 -> None; returned Candidate has index_score=None
# @schema: Candidate.source == "openalex"; authors are family names derived from author display_name (last token, or first token when the last is an initial)
import dataclasses
import html
import re
from urllib.parse import quote

from citeproof.config import Config
from citeproof.httpclient import HttpError, get_json
from citeproof.schemas import Candidate

_SELECT = "id,doi,title,display_name,authorships,publication_year,primary_location,biblio,relevance_score"
_TAGS = re.compile(r"<[^>]+>")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_DOI_PREFIX = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)


def family_name(display_name: str) -> str | None:
    parts = display_name.replace(",", " ").split()
    while parts and parts[-1].strip(".").casefold() in _SUFFIXES:
        parts.pop()
    if not parts:
        return None
    last = parts[-1].strip(".")
    if len(last) <= 1 and len(parts) > 1:
        return parts[0]
    return parts[-1]


def parse_item(item: dict) -> Candidate | None:
    title = item.get("title") or item.get("display_name")
    if not isinstance(title, str):
        return None
    title = " ".join(html.unescape(_TAGS.sub("", title)).split())
    if title == "":
        return None

    authors: list[str] = []
    for a in item.get("authorships") or []:
        name = ((a or {}).get("author") or {}).get("display_name")
        if isinstance(name, str):
            fam = family_name(name)
            if fam:
                authors.append(fam)
        if len(authors) == 20:
            break

    doi_raw = item.get("doi")
    doi = None
    if isinstance(doi_raw, str) and doi_raw.strip():
        doi = _DOI_PREFIX.sub("", doi_raw.strip()).lower()

    year = item.get("publication_year")
    year = year if isinstance(year, int) and not isinstance(year, bool) else None

    source = ((item.get("primary_location") or {}).get("source") or {})
    venue = source.get("display_name") if isinstance(source.get("display_name"), str) else None

    biblio = item.get("biblio") or {}
    volume = biblio.get("volume") if isinstance(biblio.get("volume"), str) and biblio.get("volume") else None
    first, last = biblio.get("first_page"), biblio.get("last_page")
    pages = None
    if isinstance(first, str) and first:
        pages = f"{first}-{last}" if isinstance(last, str) and last and last != first else first

    url = f"https://doi.org/{doi}" if doi else (item.get("id") if isinstance(item.get("id"), str) else None)
    score = item.get("relevance_score")
    index_score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    return Candidate(
        source="openalex", doi=doi, title=title, authors=authors, year=year,
        venue=venue, volume=volume, pages=pages, url=url, index_score=index_score,
    )


def search(raw_ref: str, cfg: Config, per_page: int = 5) -> tuple[list[Candidate], int]:
    query = " ".join(raw_ref.replace("*", "").replace("?", "").split())[:200]
    if query == "":
        raise ValueError("openalex.search was given an empty reference string. Pass the reference text.")
    params = {"search": query, "per_page": str(per_page), "select": _SELECT, "mailto": cfg.mailto}
    data, made = get_json(f"{cfg.openalex_base}/works", params, cfg)
    if data is None:
        raise HttpError(f"OpenAlex search returned HTTP 404 for {cfg.openalex_base}/works. Check that openalex_base is correct.")
    cands = [c for c in (parse_item(i) for i in data.get("results") or []) if c is not None]
    return cands, made


def lookup_doi(doi: str, cfg: Config) -> tuple[Candidate | None, int]:
    url = f"{cfg.openalex_base}/works/https://doi.org/{quote(doi, safe='/')}"
    data, made = get_json(url, {"select": _SELECT, "mailto": cfg.mailto}, cfg)
    if data is None:
        return None, made
    cand = parse_item(data)
    if cand is None:
        return None, made
    return dataclasses.replace(cand, index_score=None), made
