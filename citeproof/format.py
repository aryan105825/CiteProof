# @exports: STYLES: tuple[str, ...] = ("apa", "ieee", "vancouver", "messy")
# @exports: format_ref(c: Candidate, style: str, rng: random.Random | None = None) -> str
# @imports: schemas.py:Candidate
# @env: none
# @schema: style "apa"|"ieee"|"vancouver" are deterministic and include the DOI when the record has one; style "messy" requires rng (random.Random) and is deterministic for a given rng state
# @schema: Candidate.authors holds family names only, so no initials are ever produced
import dataclasses
import random

from citeproof.schemas import Candidate

STYLES = ("apa", "ieee", "vancouver", "messy")
_BASE_STYLES = ("apa", "ieee", "vancouver")


def _end(s: str) -> str:
    s = s.strip()
    return s if s and s[-1] in ".?!" else s + "."


def _apa_names(authors: list[str]) -> str:
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} & {authors[1]}"
    return ", ".join(authors[:-1]) + ", & " + authors[-1]


def _ieee_names(authors: list[str]) -> str:
    if len(authors) > 6:
        return f"{authors[0]} et al."
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} and {authors[1]}"
    return ", ".join(authors[:-1]) + ", and " + authors[-1]


def _vancouver_names(authors: list[str]) -> str:
    # returned WITHOUT a trailing period; the caller adds one
    if len(authors) > 6:
        return ", ".join(authors[:6]) + ", et al"
    return ", ".join(authors)


def _format(c: Candidate, style: str, include_doi: bool, short_authors: bool) -> str:
    authors = list(c.authors)
    if style == "apa":
        if not authors:
            names = ""
        elif short_authors:
            names = f"{authors[0]} et al."
        else:
            names = _apa_names(authors)
        year = f"({c.year})." if c.year is not None else "(n.d.)."
        parts = [(names + " " if names else "") + year, _end(c.title)]
        if c.venue:
            venue = c.venue
            if c.volume:
                venue += f", {c.volume}"
            if c.pages:
                venue += f", {c.pages}"
            parts.append(venue + ".")
        if include_doi and c.doi:
            parts.append(f"https://doi.org/{c.doi}")
        return " ".join(parts)

    if style == "ieee":
        if not authors:
            names = ""
        elif short_authors:
            names = f"{authors[0]} et al."
        else:
            names = _ieee_names(authors)
        tail = []
        if c.venue:
            tail.append(c.venue)
        if c.volume:
            tail.append(f"vol. {c.volume}")
        if c.pages:
            tail.append(f"pp. {c.pages}")
        if c.year is not None:
            tail.append(str(c.year))
        s = (names + ", " if names else "") + f'"{c.title.rstrip(".,")},"'
        if tail:
            s += " " + ", ".join(tail) + "."
        if include_doi and c.doi:
            s += f" doi: {c.doi}."
        return s

    if style == "vancouver":
        if not authors:
            names = ""
        elif short_authors:
            names = f"{authors[0]} et al"
        else:
            names = _vancouver_names(authors)
        s = (names + ". " if names else "") + _end(c.title)
        if c.venue:
            s += " " + _end(c.venue)
        if c.year is not None:
            tail = str(c.year)
            if c.volume:
                tail += f";{c.volume}"
            if c.pages:
                tail += f":{c.pages}"
            s += " " + tail + "."
        if include_doi and c.doi:
            s += f" doi:{c.doi}"
        return s

    raise ValueError(f"Unknown base style {style!r}; expected one of {_BASE_STYLES}.")


def format_ref(c: Candidate, style: str, rng: random.Random | None = None) -> str:
    if style in _BASE_STYLES:
        return _format(c, style, include_doi=True, short_authors=False)
    if style == "messy":
        if rng is None:
            raise ValueError('format_ref(style="messy") needs rng=random.Random(seed) so output is reproducible.')
        base = rng.choice(_BASE_STYLES)
        r_doi = rng.random()
        r_short = rng.random()
        r_pages = rng.random()
        r_vol = rng.random()
        c2 = dataclasses.replace(
            c,
            pages=None if r_pages < 0.3 else c.pages,
            volume=None if r_vol < 0.3 else c.volume,
        )
        short = r_short < 0.4 and len(c.authors) > 2
        return _format(c2, base, include_doi=r_doi >= 0.6, short_authors=short)
    raise ValueError(f"Unknown style {style!r}; expected one of {STYLES}.")
