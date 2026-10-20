# @exports: MODES: tuple[str, ...] = ("weak", "strong", "evidence")
# @exports: gather_candidates(raw: str, cfg: Config) -> Gathered
# @exports: judge(ref_id: int, raw: str, g: Gathered, th: Thresholds, mode: str = "evidence") -> Verdict
# @exports: verify_reference(ref_id: int, raw: str, cfg: Config, th: Thresholds, mode: str = "evidence") -> Verdict
# @exports: verify_many(refs: list[str], cfg: Config, th: Thresholds, mode: str = "evidence", workers: int = 3) -> list[Verdict]
# @exports: verify_compare(refs: list[str], cfg: Config, th: Thresholds, workers: int = 3) -> dict
# @exports: report_dict(verdicts: list[Verdict], mode: str) -> dict
# @imports: baseline.py:baseline_verdict, top_by_index_score
# @imports: config.py:Config, Thresholds
# @imports: crossref.py:search, lookup_doi
# @imports: format.py:format_ref
# @imports: schemas.py:Gathered, Verdict
# @imports: textnorm.py:extract_doi, fold
# @imports: openalex.py:search, lookup_doi (M2, imported lazily; only used when cfg.use_openalex)
# @imports: scoring.py:rank_and_evaluate, decide (M2, imported lazily; only used by mode "evidence")
# @env: none
# @schema: report_dict -> {"mode": str, "summary": {"total","verified","mismatch","not_found","http_requests"}, "items": [asdict(Verdict)]}
# @schema: verify_compare -> {"weak": report_dict, "strong": report_dict, "evidence": report_dict}, one gather per reference
# @schema: ref_id is 1-based position in the input list; Gathered.cands excludes doi_resolved (scoring must consider g.doi_resolved itself)
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from rapidfuzz import fuzz

from citeproof import crossref
from citeproof.baseline import baseline_verdict, top_by_index_score
from citeproof.config import Config, Thresholds
from citeproof.format import format_ref
from citeproof.schemas import Gathered, Verdict
from citeproof.textnorm import extract_doi, fold

MODES = ("weak", "strong", "evidence")
OPENALEX_FALLBACK_TITLE_SIM = 85.0


def _optional_module(name: str, hint: str):
    full = f"citeproof.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name != full:
            raise
        raise RuntimeError(
            f"{full.replace('.', '/')}.py is not in the repo yet (it is added in milestone M2). {hint}"
        ) from e


def _openalex():
    return _optional_module("openalex", "Set CITEPROOF_OPENALEX=0 to run Crossref-only until then.")


def _scoring():
    return _optional_module("scoring", 'Use --mode weak or --mode strong until then.')


def _mark(sources: list[str], name: str) -> None:
    if name not in sources:
        sources.append(name)


def _validate_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {MODES}.")
    if mode == "evidence":
        _scoring()


def gather_candidates(raw: str, cfg: Config) -> Gathered:
    t0 = time.perf_counter()
    made = 0
    sources: list[str] = []
    doi_in = extract_doi(raw)
    doi_resolved = None

    if doi_in is not None:
        doi_resolved, n = crossref.lookup_doi(doi_in, cfg)
        made += n
        if doi_resolved is not None:
            _mark(sources, "crossref")
        elif cfg.use_openalex:
            doi_resolved, n = _openalex().lookup_doi(doi_in, cfg)
            made += n
            if doi_resolved is not None:
                _mark(sources, "openalex")

    cands, n = crossref.search(raw, cfg)
    made += n
    _mark(sources, "crossref")

    if cfg.use_openalex:
        top = top_by_index_score(cands)
        sim = float(fuzz.partial_ratio(fold(top.title), fold(raw))) if top is not None else 0.0
        if sim < OPENALEX_FALLBACK_TITLE_SIM:
            extra, n = _openalex().search(raw, cfg)
            made += n
            cands = cands + extra
            _mark(sources, "openalex")

    return Gathered(
        cands=cands,
        doi_in_string=doi_in,
        doi_resolved=doi_resolved,
        sources_used=sources,
        http_requests=made,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


def judge(ref_id: int, raw: str, g: Gathered, th: Thresholds, mode: str = "evidence") -> Verdict:
    _validate_mode(mode)
    if mode == "weak":
        return baseline_verdict(ref_id, raw, g, th.baseline_title_weak, "title")
    if mode == "strong":
        return baseline_verdict(ref_id, raw, g, th.baseline_title_strong, "title_author")

    scoring = _scoring()
    best, ev = scoring.rank_and_evaluate(raw, g, th)
    if best is None or ev is None:
        return Verdict(
            ref_id=ref_id, raw=raw, label="NOT_FOUND", reasons=["NO_CANDIDATE"], confidence=1.0,
            best=None, evidence=None, suggested=None,
            http_requests=g.http_requests, elapsed_ms=g.elapsed_ms,
        )
    label, reasons, confidence = scoring.decide(ev, th)
    suggested = format_ref(best, "apa") if label == "MISMATCH" else None
    return Verdict(
        ref_id=ref_id, raw=raw, label=label, reasons=reasons, confidence=confidence,
        best=best, evidence=ev, suggested=suggested,
        http_requests=g.http_requests, elapsed_ms=g.elapsed_ms,
    )


def verify_reference(ref_id: int, raw: str, cfg: Config, th: Thresholds, mode: str = "evidence") -> Verdict:
    _validate_mode(mode)
    return judge(ref_id, raw, gather_candidates(raw, cfg), th, mode)


def _check_batch(refs: list[str], workers: int) -> None:
    if not refs:
        raise ValueError("No references to check. Pass at least one reference string.")
    if workers < 1:
        raise ValueError(f"workers must be at least 1, got {workers}.")


def verify_many(refs: list[str], cfg: Config, th: Thresholds, mode: str = "evidence", workers: int = 3) -> list[Verdict]:
    _validate_mode(mode)
    _check_batch(refs, workers)

    def work(item: tuple[int, str]) -> Verdict:
        ref_id, raw = item
        return verify_reference(ref_id, raw, cfg, th, mode)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(work, enumerate(refs, start=1)))


def verify_compare(refs: list[str], cfg: Config, th: Thresholds, workers: int = 3) -> dict:
    _validate_mode("evidence")
    _check_batch(refs, workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        gathered = list(pool.map(lambda raw: gather_candidates(raw, cfg), refs))
    out = {}
    for mode in MODES:
        verdicts = [judge(i, raw, g, th, mode) for i, (raw, g) in enumerate(zip(refs, gathered), start=1)]
        out[mode] = report_dict(verdicts, mode)
    return out


def report_dict(verdicts: list[Verdict], mode: str) -> dict:
    return {
        "mode": mode,
        "summary": {
            "total": len(verdicts),
            "verified": sum(1 for v in verdicts if v.label == "VERIFIED"),
            "mismatch": sum(1 for v in verdicts if v.label == "MISMATCH"),
            "not_found": sum(1 for v in verdicts if v.label == "NOT_FOUND"),
            "http_requests": sum(v.http_requests for v in verdicts),
        },
        "items": [asdict(v) for v in verdicts],
    }
