"""Name normalization — the single spelling every source's player names collapse to.

Every external source spells a name its own way: ESPN says "O.G. Anunoby", nba.com says "OG
Anunoby", a projections CSV says "Anunoby, O.G.". Normalizing both sides to one plain-ASCII,
punctuation-free, suffix-free form turns most of that into an exact hit, and leaves fuzzy
matching to handle only the genuinely hard tail.

Pure functions, no I/O — the rules live here so every importer applies exactly the same ones.
"""

import re
import unicodedata

# Trailing generational suffixes. Sources disagree about them constantly ("Jaren Jackson Jr."
# vs "Jaren Jackson"), and they never distinguish two active NBA players, so they come off.
# Kept as a frozenset of already-normalized tokens: punctuation is stripped before this runs.
GENERATIONAL_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v", "junior", "senior"})

# Anything that isn't a letter, digit, or space becomes a space: periods ("O.G." -> "o g"),
# apostrophes ("De'Aaron" -> "de aaron"), hyphens ("Karl-Anthony" -> "karl anthony"), commas.
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_WHITESPACE = re.compile(r"\s+")


# Latin letters that carry a stroke or bar rather than a combining accent. NFKD does not
# decompose these — "Đurišić" would lose its D entirely — so they are transliterated by hand.
# Đ and Ø turn up in NBA rosters often enough to matter (Nikola Đurišić, Kristaps Porziņģis's
# Baltic neighbours), and the rest cost nothing to include.
_TRANSLITERATIONS = str.maketrans(
    {
        "Đ": "D",
        "đ": "d",
        "Ð": "D",
        "ð": "d",
        "Ø": "O",
        "ø": "o",
        "Œ": "OE",
        "œ": "oe",
        "Æ": "AE",
        "æ": "ae",
        "Ł": "L",
        "ł": "l",
        "Þ": "Th",
        "þ": "th",
        "ß": "ss",
        "ı": "i",
    }
)


def strip_accents(text: str) -> str:
    """Fold a name to plain ASCII letters: "Dončić" -> "Doncic", "Đurišić" -> "Durisic"."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATIONS))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def strip_suffixes(tokens: list[str]) -> list[str]:
    """Drop trailing generational suffixes, but never the whole name.

    Iterative, because "Jr. II" and friends exist. The guard matters for real players whose
    surname *is* a suffix token — dropping every token would leave nothing to match on.
    """
    while len(tokens) > 1 and tokens[-1] in GENERATIONAL_SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def name_tokens(name: str) -> list[str]:
    """The normalized tokens of a name, in order."""
    folded = _NON_ALNUM.sub(" ", strip_accents(name).lower())
    return strip_suffixes(_WHITESPACE.sub(" ", folded).strip().split())


def normalize_name(name: str) -> str:
    """The canonical spelling of a player name, used as the match key.

    >>> normalize_name("Luka Dončić")
    'luka doncic'
    >>> normalize_name("Jaren Jackson Jr.")
    'jaren jackson'
    >>> normalize_name("De'Aaron Fox")
    'de aaron fox'
    >>> normalize_name("O.G. Anunoby")
    'o g anunoby'
    >>> normalize_name("Karl-Anthony Towns")
    'karl anthony towns'
    """
    return " ".join(name_tokens(name))


def compact_name(name: str) -> str:
    """The normalized name with its spaces removed too: "O.G. Anunoby" -> "oganunoby".

    Two names that agree here differ by nothing but punctuation and spacing — "J.J. O'Brien"
    and "JJ O'Brien", "De'Aaron Fox" and "DeAaron Fox". That is the single most common way
    sources disagree, and it is worth resolving as a certainty rather than as a similarity
    score: the letters are identical, so there is no guess involved. Contrast "Mike Brown" and
    "Mikel Brown", which score 0.95 on similarity but are two different people — and which
    this never conflates, because their letters differ.
    """
    return "".join(name_tokens(name))


def uninvert_name(name: str) -> str | None:
    """Turn "Anunoby, O.G." into "O.G. Anunoby", or None if the name isn't inverted.

    CSV exports love `Last, First`, and normalization alone can't fix it: the tokens are all
    there but in the wrong order, so the normalized key misses. The matcher tries this form as
    a second spelling rather than folding it into `normalize_name`, which stays a pure
    character-level rule set.
    """
    surname, separator, given = name.partition(",")
    if not separator or "," in given:
        return None
    surname, given = surname.strip(), given.strip()
    if not surname or not given:
        return None
    return f"{given} {surname}"


def last_token(name: str) -> str:
    """The last normalized token — the surname, near enough, after suffixes come off.

    Used as a guard on fuzzy matches: two names that agree on nothing at the end are almost
    never the same player, however close the string similarity looks.
    """
    tokens = name_tokens(name)
    return tokens[-1] if tokens else ""
