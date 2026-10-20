# @exports: Candidate(source, doi, title, authors, year, venue, volume, pages, url, index_score) frozen dataclass
# @exports: Gathered(cands, doi_in_string, doi_resolved, sources_used, http_requests, elapsed_ms) dataclass
# @exports: FieldEvidence(title_sim, first_author_found, authors_found_frac, year_status, venue_sim, residual_frac, doi_status) dataclass
# @exports: Verdict(ref_id, raw, label, reasons, confidence, best, evidence, suggested, http_requests, elapsed_ms) dataclass
# @imports: none
# @env: none
# @schema: Candidate{source: "crossref"|"openalex", doi: str|None lowercase no URL prefix, title: str, authors: list[str] family names max 20, year: int|None, venue: str|None, volume: str|None, pages: str|None, url: str|None, index_score: float|None}
# @schema: Gathered{cands: list[Candidate] search results only (doi_resolved is NOT included), doi_in_string: str|None, doi_resolved: Candidate|None, sources_used: list[str], http_requests: int, elapsed_ms: int}
# @schema: FieldEvidence{title_sim: float 0-100, first_author_found: bool, authors_found_frac: float, year_status: "match"|"off_by_1"|"conflict"|"absent", venue_sim: float|None, residual_frac: float, doi_status: "none"|"match"|"points_elsewhere"|"unresolved"}
# @schema: Verdict{ref_id: int, raw: str, label: "VERIFIED"|"MISMATCH"|"NOT_FOUND", reasons: list[str], confidence: float 0-1, best: Candidate|None, evidence: FieldEvidence|None, suggested: str|None, http_requests: int, elapsed_ms: int}
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    source: str                 # "crossref" | "openalex"
    doi: str | None             # lowercase, no URL prefix
    title: str
    authors: list[str]          # family names, in order, max 20
    year: int | None
    venue: str | None
    volume: str | None
    pages: str | None
    url: str | None
    index_score: float | None


@dataclass
class Gathered:
    cands: list[Candidate]
    doi_in_string: str | None
    doi_resolved: Candidate | None
    sources_used: list[str]
    http_requests: int
    elapsed_ms: int


@dataclass
class FieldEvidence:
    title_sim: float            # 0-100, rapidfuzz partial_ratio(fold(cand.title), fold(raw))
    first_author_found: bool
    authors_found_frac: float   # of first min(len,6) candidate surnames found in raw
    year_status: str            # "match" | "off_by_1" | "conflict" | "absent"
    venue_sim: float | None     # informational only
    residual_frac: float        # alphabetic tokens (len>=3) of raw not prefix-explained by cand
                                # title/authors/venue, excluding venue initialism + known abbreviations
    doi_status: str             # "none" | "match" | "points_elsewhere" | "unresolved"


@dataclass
class Verdict:
    ref_id: int
    raw: str
    label: str                  # "VERIFIED" | "MISMATCH" | "NOT_FOUND"
    reasons: list[str]          # TITLE_MATCH AUTHORS_MATCH YEAR_MATCH TITLE_NOT_IN_RECORD AUTHOR_MISMATCH
                                # YEAR_CONFLICT UNEXPLAINED_TEXT DOI_POINTS_ELSEWHERE DOI_UNRESOLVED
                                # NO_CANDIDATE WEAK_CANDIDATE
    confidence: float           # 0-1
    best: Candidate | None
    evidence: FieldEvidence | None
    suggested: str | None       # formatted closest real record; set ONLY on MISMATCH
    http_requests: int
    elapsed_ms: int
