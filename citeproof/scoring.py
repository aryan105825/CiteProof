# @exports: is_preprint_venue(venue: str | None) -> bool
# @exports: compute_evidence(raw: str, cand: Candidate, doi_in_string: str | None, doi_resolved: Candidate | None, year_window_preprint: int = 2) -> FieldEvidence
# @exports: rank_and_evaluate(raw: str, g: Gathered, th: Thresholds) -> tuple[Candidate | None, FieldEvidence | None]
# @exports: decide(ev: FieldEvidence, th: Thresholds) -> tuple[str, list[str], float]
# @imports: config.py:Thresholds
# @imports: schemas.py:Candidate, FieldEvidence, Gathered
# @imports: textnorm.py:STOPWORDS, extract_years, fold, surname_in, tokens, venue_initialism
# @env: none
# @schema: compute_evidence adds optional year_window_preprint (beyond CONTRACTS): off_by_1 means |year diff| <= 1, or <= year_window_preprint when cand.venue is a preprint server; rank_and_evaluate passes th.year_tol_preprint
# @schema: rank_and_evaluate ranks g.cands plus g.doi_resolved (appended if absent) by 0.5*title_sim + 30*authors_found_frac + 10*(year match) + 10*(doi match); th is accepted for contract compatibility and unused
# @schema: decide: VERIFIED if T&A&Y&R&D, or doi_status=="match" & T&A&Y; NOT_FOUND if not Tp and not A; else MISMATCH with one reason per failed part (year absent is reported as YEAR_CONFLICT)
# @schema: residual_frac: alphabetic raw tokens (len>=3, not stopwords) not explained by a cand title/author/venue token prefix, a venue initialism, a known abbreviation or month, or adjacency to a matched surname (given names)
import re

from rapidfuzz import fuzz

from citeproof.config import Thresholds
from citeproof.schemas import Candidate, FieldEvidence, Gathered
from citeproof.textnorm import STOPWORDS, extract_years, fold, surname_in, tokens, venue_initialism

_PREPRINT_RE = re.compile(r"\b(arxiv|biorxiv|medrxiv|chemrxiv|ssrn|preprints?|research square|osf)\b")
_MONTHS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})
_KNOWN_ABBREVIATIONS = frozenset({
    "proc", "conf", "int", "intl", "trans", "symp", "ieee", "acm", "arxiv", "preprint", "eds", "edn",
    "pdf", "url", "accessed", "available", "online", "retrieved", "suppl", "supplement", "vol", "pp",
}) | _MONTHS


def is_preprint_venue(venue: str | None) -> bool:
    return bool(venue) and _PREPRINT_RE.search(fold(venue)) is not None


def _year_status(raw: str, cand: Candidate, preprint_window: int) -> str:
    years = extract_years(raw)
    if cand.year is None or not years:
        return "absent"
    if cand.year in years:
        return "match"
    window = preprint_window if is_preprint_venue(cand.venue) else 1
    if any(abs(y - cand.year) <= window for y in years):
        return "off_by_1"
    return "conflict"


def _residual_frac(raw: str, cand: Candidate) -> float:
    raw_tokens = fold(raw).split()
    author_tokens = set(tokens(" ".join(cand.authors)))
    cand_tokens = set(tokens(cand.title)) | author_tokens | set(tokens(cand.venue or ""))
    initialism = venue_initialism(cand.venue)

    adjacent: set[int] = set()
    for i, t in enumerate(raw_tokens):
        if t in author_tokens:
            adjacent.update((i - 1, i + 1))

    total = 0
    unexplained = 0
    for i, t in enumerate(raw_tokens):
        if len(t) < 3 or not t.isalpha() or t in STOPWORDS:
            continue
        total += 1
        explained = (
            i in adjacent
            or t in _KNOWN_ABBREVIATIONS
            or any(u.startswith(t) for u in cand_tokens)
            or (initialism is not None and t in initialism)
        )
        if not explained:
            unexplained += 1
    return 0.0 if total == 0 else unexplained / total


def _doi_status(cand: Candidate, doi_in_string: str | None, doi_resolved: Candidate | None) -> str:
    if doi_in_string is None:
        return "none"
    if doi_resolved is None:
        return "unresolved"
    if cand.doi is not None and (cand.doi == doi_in_string or cand.doi == doi_resolved.doi):
        return "match"
    return "points_elsewhere"


def compute_evidence(
    raw: str,
    cand: Candidate,
    doi_in_string: str | None,
    doi_resolved: Candidate | None,
    year_window_preprint: int = 2,
) -> FieldEvidence:
    folded_raw = fold(raw)
    names = cand.authors[:6]
    found = [surname_in(a, folded_raw) for a in names]
    return FieldEvidence(
        title_sim=float(fuzz.partial_ratio(fold(cand.title), folded_raw)),
        first_author_found=bool(found) and found[0],
        authors_found_frac=(sum(found) / len(names)) if names else 0.0,
        year_status=_year_status(raw, cand, year_window_preprint),
        venue_sim=float(fuzz.partial_ratio(fold(cand.venue), folded_raw)) if cand.venue else None,
        residual_frac=_residual_frac(raw, cand),
        doi_status=_doi_status(cand, doi_in_string, doi_resolved),
    )


def rank_and_evaluate(raw: str, g: Gathered, th: Thresholds) -> tuple[Candidate | None, FieldEvidence | None]:
    pool = list(g.cands)
    resolved = g.doi_resolved
    if resolved is not None and (resolved.doi is None or all(c.doi != resolved.doi for c in pool)):
        pool.append(resolved)

    best: Candidate | None = None
    best_ev: FieldEvidence | None = None
    best_score = float("-inf")
    for cand in pool:
        ev = compute_evidence(raw, cand, g.doi_in_string, g.doi_resolved, th.year_tol_preprint)
        score = (
            0.5 * ev.title_sim
            + 30.0 * ev.authors_found_frac
            + (10.0 if ev.year_status == "match" else 0.0)
            + (10.0 if ev.doi_status == "match" else 0.0)
        )
        if score > best_score:
            best, best_ev, best_score = cand, ev, score
    return best, best_ev


def decide(ev: FieldEvidence, th: Thresholds) -> tuple[str, list[str], float]:
    title_ok = ev.title_sim >= th.title_ok
    title_partial = ev.title_sim >= th.title_partial
    author_ok = ev.first_author_found and ev.authors_found_frac >= th.author_frac_ok
    year_ok = ev.year_status == "match" or (ev.year_status == "off_by_1" and th.year_tol >= 1)
    residual_ok = ev.residual_frac <= th.residual_max
    doi_ok = ev.doi_status in ("none", "match")

    core_ok = title_ok and author_ok and year_ok
    if core_ok and ((residual_ok and doi_ok) or ev.doi_status == "match"):
        confidence = (
            0.5 * ev.title_sim / 100.0
            + 0.3 * ev.authors_found_frac
            + 0.2 * (1.0 - min(1.0, ev.residual_frac))
        )
        return "VERIFIED", ["TITLE_MATCH", "AUTHORS_MATCH", "YEAR_MATCH"], max(0.0, min(1.0, confidence))

    if not title_partial and not author_ok:
        return "NOT_FOUND", ["WEAK_CANDIDATE"], max(0.5, 1.0 - ev.title_sim / 100.0)

    reasons: list[str] = []
    if not title_ok:
        reasons.append("TITLE_NOT_IN_RECORD")
    if not author_ok:
        reasons.append("AUTHOR_MISMATCH")
    if not year_ok:
        reasons.append("YEAR_CONFLICT")
    if not residual_ok:
        reasons.append("UNEXPLAINED_TEXT")
    if not doi_ok:
        reasons.append("DOI_POINTS_ELSEWHERE" if ev.doi_status == "points_elsewhere" else "DOI_UNRESOLVED")
    return "MISMATCH", reasons, min(0.95, 0.5 + 0.1 * len(reasons))
