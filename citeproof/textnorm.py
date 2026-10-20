# @exports: STOPWORDS: frozenset[str]
# @exports: fold(s: str) -> str
# @exports: tokens(s: str) -> list[str]
# @exports: extract_doi(s: str) -> str | None
# @exports: extract_years(s: str) -> list[int]
# @exports: surname_in(surname: str, folded_raw: str) -> bool
# @exports: venue_initialism(venue: str | None) -> str | None
# @imports: none
# @env: none
# @schema: fold = NFKD, strip accents, casefold, non-alnum -> space, collapse; extract_years returns years 1900..2100 in order of appearance
import re
import unicodedata

STOPWORDS = frozenset({
    "the", "of", "and", "in", "on", "for", "a", "an", "to", "by", "with", "from", "at", "as", "or",
    "journal", "vol", "volume", "pp", "page", "pages", "no", "number", "issue",
    "doi", "https", "http", "www", "org", "com", "et", "al",
})

_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)")


def fold(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", stripped.casefold()).strip()


def tokens(s: str) -> list[str]:
    return [t for t in fold(s).split() if t not in STOPWORDS]


def extract_doi(s: str) -> str | None:
    m = _DOI.search(s)
    if m is None:
        return None
    doi = m.group(0).rstrip(".,;)]}>\"'").lower()
    return doi if "/" in doi else None


def extract_years(s: str) -> list[int]:
    return [int(m.group(1)) for m in _YEAR.finditer(s)]


def surname_in(surname: str, folded_raw: str) -> bool:
    folded = fold(surname)
    if folded == "":
        return False
    pattern = r"(?<![^\W_])" + re.escape(folded) + r"(?![^\W_])"
    return re.search(pattern, folded_raw) is not None


def venue_initialism(venue: str | None) -> str | None:
    if not venue:
        return None
    words = [t for t in tokens(venue) if not t.isdigit()]
    if len(words) < 2:
        return None
    return "".join(w[0] for w in words)
