# @exports: Thresholds(title_ok=88.0, title_partial=60.0, author_frac_ok=0.5, residual_max=0.45, year_tol=1, year_tol_preprint=2, baseline_title_weak=90.0, baseline_title_strong=90.0) dataclass
# @exports: Config(mailto, crossref_base, openalex_base, rate_per_sec, timeout_s, max_retries, use_cache, use_openalex) dataclass
# @exports: load_config(use_cache: bool | None = None) -> Config
# @exports: load_thresholds(path: str = "results/calibration.json") -> tuple[Thresholds, bool]
# @imports: none
# @env: CITEPROOF_MAILTO
# @env: CITEPROOF_RATE
# @env: CITEPROOF_TIMEOUT
# @env: CITEPROOF_CACHE
# @env: CITEPROOF_OPENALEX
# @schema: results/calibration.json {"thresholds": {title_ok, title_partial, author_frac_ok, residual_max, year_tol, year_tol_preprint}, "baseline_thresholds": {"weak": float, "strong": float}} (other keys ignored)
# @schema: Thresholds adds baseline_title_weak / baseline_title_strong (defaults 90.0) beyond CONTRACTS, so judge() can use calibrated baselines
import json
import os
import re
import sys
from dataclasses import dataclass, fields

CROSSREF_BASE = "https://api.crossref.org"
OPENALEX_BASE = "https://api.openalex.org"
DEFAULT_MAILTO = "anonymous@example.org"
DEFAULT_MAX_RETRIES = 3
_MAILTO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_warned_mailto = False


@dataclass
class Thresholds:
    title_ok: float = 88.0
    title_partial: float = 60.0
    author_frac_ok: float = 0.5
    residual_max: float = 0.45
    year_tol: int = 1
    year_tol_preprint: int = 2          # applies when cand.venue matches a preprint server
    baseline_title_weak: float = 90.0   # calibrated on TRAIN by bench; used by judge(mode="weak")
    baseline_title_strong: float = 90.0  # calibrated on TRAIN by bench; used by judge(mode="strong")


@dataclass
class Config:
    mailto: str
    crossref_base: str
    openalex_base: str
    rate_per_sec: float
    timeout_s: float
    max_retries: int
    use_cache: bool
    use_openalex: bool


def _env_positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}. Fix it in your .env (see .env.example).") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {raw!r}. Fix it in your .env (see .env.example).")
    return value


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    if raw not in ("0", "1"):
        raise ValueError(f"{name} must be 0 or 1, got {raw!r}. Fix it in your .env (see .env.example).")
    return raw == "1"


def load_config(use_cache: bool | None = None) -> Config:
    global _warned_mailto
    mailto = os.environ.get("CITEPROOF_MAILTO", "").strip()
    if mailto == "":
        if not _warned_mailto:
            print(
                f"WARN: CITEPROOF_MAILTO is unset; using {DEFAULT_MAILTO}. "
                "Set it in .env to join the Crossref polite pool.",
                file=sys.stderr,
            )
            _warned_mailto = True
        mailto = DEFAULT_MAILTO
    elif not _MAILTO_RE.match(mailto):
        raise ValueError(f"CITEPROOF_MAILTO must be an email address, got {mailto!r}. Fix it in your .env.")

    rate = _env_positive_float("CITEPROOF_RATE", 3.0)
    timeout = _env_positive_float("CITEPROOF_TIMEOUT", 15.0)
    env_cache = _env_flag("CITEPROOF_CACHE", False)
    use_openalex = _env_flag("CITEPROOF_OPENALEX", True)
    return Config(
        mailto=mailto,
        crossref_base=CROSSREF_BASE,
        openalex_base=OPENALEX_BASE,
        rate_per_sec=rate,
        timeout_s=timeout,
        max_retries=DEFAULT_MAX_RETRIES,
        use_cache=env_cache if use_cache is None else use_cache,
        use_openalex=use_openalex,
    )


def load_thresholds(path: str = "results/calibration.json") -> tuple[Thresholds, bool]:
    if not os.path.exists(path):
        return Thresholds(), False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e}). Re-run `python -m bench.run_benchmark` to regenerate it.") from e
    th_raw = data.get("thresholds") if isinstance(data, dict) else None
    if not isinstance(th_raw, dict):
        raise ValueError(f'{path} has no "thresholds" object. Re-run `python -m bench.run_benchmark` to regenerate it.')
    kwargs = dict(th_raw)
    base = data.get("baseline_thresholds")
    if base is not None:
        if not isinstance(base, dict) or "weak" not in base or "strong" not in base:
            raise ValueError(f'{path}: "baseline_thresholds" must be an object with "weak" and "strong".')
        kwargs["baseline_title_weak"] = base["weak"]
        kwargs["baseline_title_strong"] = base["strong"]
    defaults = Thresholds()
    known = {f.name for f in fields(Thresholds)}
    unknown = set(kwargs) - known
    if unknown:
        raise ValueError(f"{path}: unknown threshold keys {sorted(unknown)}; expected a subset of {sorted(known)}.")
    coerced = {}
    for name, value in kwargs.items():
        want = type(getattr(defaults, name))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path}: threshold {name} must be a number, got {value!r}.")
        if want is int:
            if value != int(value):
                raise ValueError(f"{path}: threshold {name} must be an integer, got {value!r}.")
            coerced[name] = int(value)
        else:
            coerced[name] = float(value)
    return Thresholds(**coerced), True
