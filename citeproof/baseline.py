# @exports: top_by_index_score(cands: list[Candidate]) -> Candidate | None
# @exports: baseline_verdict(ref_id: int, raw: str, g: Gathered, title_threshold: float = 90.0, variant: str = "title") -> Verdict
# @imports: schemas.py:Candidate, Gathered, Verdict
# @imports: textnorm.py:fold, surname_in
# @env: none
# @schema: variant "title" (WEAK): VERIFIED iff partial_ratio(fold(top.title), fold(raw)) >= title_threshold, else NOT_FOUND
# @schema: variant "title_author" (STRONG): additionally requires the first author surname in raw
# @schema: neither variant emits MISMATCH; top-1 is chosen by index_score among Crossref candidates if any, else among all candidates (scores are not comparable across sources)
from rapidfuzz import fuzz

from citeproof.schemas import Candidate, Gathered, Verdict
from citeproof.textnorm import fold, surname_in

_VARIANTS = ("title", "title_author")


def top_by_index_score(cands: list[Candidate]) -> Candidate | None:
    pool = [c for c in cands if c.source == "crossref"] or list(cands)
    best: Candidate | None = None
    best_score = float("-inf")
    for c in pool:
        score = c.index_score if c.index_score is not None else float("-inf")
        if best is None or score > best_score:
            best, best_score = c, score
    return best


def baseline_verdict(
    ref_id: int,
    raw: str,
    g: Gathered,
    title_threshold: float = 90.0,
    variant: str = "title",
) -> Verdict:
    if variant not in _VARIANTS:
        raise ValueError(f"Unknown baseline variant {variant!r}; expected one of {_VARIANTS}.")
    if not 0.0 <= title_threshold <= 100.0:
        raise ValueError(f"title_threshold must be between 0 and 100, got {title_threshold}.")

    cand = top_by_index_score(g.cands)
    if cand is None:
        return Verdict(
            ref_id=ref_id, raw=raw, label="NOT_FOUND", reasons=["NO_CANDIDATE"], confidence=1.0,
            best=None, evidence=None, suggested=None,
            http_requests=g.http_requests, elapsed_ms=g.elapsed_ms,
        )

    folded_raw = fold(raw)
    sim = float(fuzz.partial_ratio(fold(cand.title), folded_raw))
    title_ok = sim >= title_threshold
    author_ok = True
    if variant == "title_author":
        author_ok = bool(cand.authors) and surname_in(cand.authors[0], folded_raw)

    reasons: list[str] = []
    reasons.append("TITLE_MATCH" if title_ok else "WEAK_CANDIDATE")
    if variant == "title_author":
        reasons.append("AUTHORS_MATCH" if author_ok else "AUTHOR_MISMATCH")

    if title_ok and author_ok:
        return Verdict(
            ref_id=ref_id, raw=raw, label="VERIFIED", reasons=reasons, confidence=sim / 100.0,
            best=cand, evidence=None, suggested=None,
            http_requests=g.http_requests, elapsed_ms=g.elapsed_ms,
        )
    confidence = 1.0 - sim / 100.0 if not title_ok else 0.5
    return Verdict(
        ref_id=ref_id, raw=raw, label="NOT_FOUND", reasons=reasons, confidence=confidence,
        best=None, evidence=None, suggested=None,
        http_requests=g.http_requests, elapsed_ms=g.elapsed_ms,
    )
