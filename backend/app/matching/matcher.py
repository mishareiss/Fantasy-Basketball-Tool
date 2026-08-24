"""Resolve any external source's player name to our canonical, ESPN-keyed `Player`.

This is shared infrastructure, not an nba.com helper: the age sync uses it today, and the
CSV/paste importer (ADP, projections, rankings, market lines) uses the same object tomorrow.
Hence the deliberate shape:

* It is built from plain `CanonicalPlayer` records, so it can be constructed from the database
  or from a literal list in a test.
* It **never writes**. `match()` returns a `MatchResult` and the caller decides whether that
  is good enough to persist as a `PlayerAlias`. An importer that wants a human to confirm a
  0.90 match can do that; the age sync auto-accepts. Neither behaviour is baked in here.
* Every result carries `confidence` and `method`, which is exactly what `PlayerAlias` records,
  so a shaky auto-match stays spottable months later.

Resolve order, most trustworthy first: a recorded alias, an exact name, a normalized name, a
fuzzy name. Ties are broken by team and position; a tie that survives that is reported as
`ambiguous` rather than guessed at, because silently attaching a projection to the wrong Anthony
Davis is worse than leaving one row unresolved.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.matching.names import compact_name, name_tokens, normalize_name, uninvert_name

# A fuzzy match has to clear this to be accepted at all. 0.88 sits above the noise (different
# players who share a surname land in the 0.6-0.8 band) and below the real variants we want to
# catch — "OG Anunoby" vs "O.G. Anunoby" (0.95), "Nic Claxton" vs "Nicolas Claxton" (0.90).
DEFAULT_THRESHOLD = 0.88

# ...and it has to beat the runner-up by this much, or the two are reported as ambiguous.
# Without it, "Marcus Morris" against a pool holding both Morris twins would coin-flip.
DEFAULT_MARGIN = 0.03

# Match methods, most to least trustworthy. `ambiguous` and `unmatched` both mean "no id".
METHOD_ALIAS = "alias"
METHOD_EXACT = "exact"
METHOD_NORMALIZED = "normalized"
METHOD_FUZZY = "fuzzy"
METHOD_AMBIGUOUS = "ambiguous"
METHOD_UNMATCHED = "unmatched"


def similarity(left: str, right: str) -> float:
    """String similarity of two *already normalized* names, in 0.0-1.0.

    `token_sort_ratio` is taken alongside the plain ratio so a source that writes
    "Anunoby, O.G." scores the same as one that writes "O.G. Anunoby" — comma-inverted names
    are common in exports and are not a genuinely hard match.

    Swapping the scorer is a one-line change: pass a different callable as `PlayerMatcher(...,
    scorer=...)`. rapidfuzz is used because it is fast enough to score a 1,000-name pool
    against 5,000 candidates without anyone noticing; `difflib.SequenceMatcher.ratio` is a
    drop-in replacement if the dependency ever becomes inconvenient.
    """
    return max(fuzz.ratio(left, right), fuzz.token_sort_ratio(left, right)) / 100.0


@dataclass(frozen=True)
class CanonicalPlayer:
    """One of our `Player` rows, reduced to what matching actually needs."""

    player_id: int
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    nba_team: str | None = None
    positions: tuple[str, ...] = ()
    # Sources tie-break towards players who are currently relevant; the nba.com static roster
    # is the one that needs it (it carries every player since 1946).
    is_active: bool = True


@dataclass(frozen=True)
class MatchCandidate:
    """A canonical player the source name could plausibly be, and how well it scored."""

    player_id: int
    full_name: str
    nba_team: str | None
    score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "player_id": self.player_id,
            "full_name": self.full_name,
            "nba_team": self.nba_team,
            "score": round(self.score, 4),
        }


@dataclass(frozen=True)
class MatchResult:
    """What the matcher concluded. `player_id is None` means nothing was resolved."""

    source_name: str
    player_id: int | None
    confidence: float
    method: str
    candidates: tuple[MatchCandidate, ...] = ()

    @property
    def matched(self) -> bool:
        return self.player_id is not None

    @property
    def needs_review(self) -> bool:
        """True when a human has to look: an ambiguity, or nothing found at all."""
        return self.method in (METHOD_AMBIGUOUS, METHOD_UNMATCHED)


@dataclass
class AliasIndex:
    """Recorded (source, name) and (source, id) mappings, consulted before any guessing.

    A hand-made alias is the whole point of the escape hatch: once someone resolves "Nah'Shon
    Hyland" -> Bones Hyland, no later run re-guesses it.
    """

    by_name: dict[tuple[str, str], int] = field(default_factory=dict)
    by_source_id: dict[tuple[str, str], int] = field(default_factory=dict)

    def add(self, source: str, source_name: str, source_id: str | None, player_id: int) -> None:
        self.by_name[(source, normalize_name(source_name))] = player_id
        if source_id:
            self.by_source_id[(source, str(source_id))] = player_id

    def lookup(self, source: str | None, source_name: str, source_id: str | None) -> int | None:
        if not source:
            return None
        if source_id is not None:
            hit = self.by_source_id.get((source, str(source_id)))
            if hit is not None:
                return hit
        return self.by_name.get((source, normalize_name(source_name)))


class PlayerMatcher:
    """Matches source names against a fixed set of canonical players. Read-only."""

    def __init__(
        self,
        players: Iterable[CanonicalPlayer],
        *,
        aliases: AliasIndex | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        margin: float = DEFAULT_MARGIN,
        scorer: Callable[[str, str], float] = similarity,
    ) -> None:
        self.players: tuple[CanonicalPlayer, ...] = tuple(players)
        self.aliases = aliases or AliasIndex()
        self.threshold = threshold
        self.margin = margin
        self.scorer = scorer

        self._by_id = {player.player_id: player for player in self.players}
        self._by_exact: dict[str, list[CanonicalPlayer]] = {}
        self._by_normalized: dict[str, list[CanonicalPlayer]] = {}
        self._by_compact: dict[str, list[CanonicalPlayer]] = {}
        # Normalization is not free, and the fuzzy tier asks for it a lot: matching the whole
        # nba.com roster is ~5,000 names against ~1,000 players. Normalize each canonical name
        # once here, and bucket by surname so a fuzzy pass compares against a handful of
        # plausible players instead of the entire pool.
        self._normalized: dict[int, str] = {}
        self._by_surname: dict[str, list[CanonicalPlayer]] = {}
        for player in self.players:
            tokens = name_tokens(player.full_name)
            normalized = " ".join(tokens)
            surname = tokens[-1] if tokens else ""
            self._normalized[player.player_id] = normalized
            self._by_exact.setdefault(player.full_name.strip(), []).append(player)
            self._by_normalized.setdefault(normalized, []).append(player)
            self._by_compact.setdefault("".join(tokens), []).append(player)
            self._by_surname.setdefault(surname, []).append(player)

    def __len__(self) -> int:
        return len(self.players)

    def get(self, player_id: int) -> CanonicalPlayer | None:
        return self._by_id.get(player_id)

    def match(
        self,
        source_name: str,
        *,
        team: str | None = None,
        positions: Sequence[str] | None = None,
        source: str | None = None,
        source_id: str | None = None,
    ) -> MatchResult:
        """Resolve one source name. `team`/`positions` are hints, used only to break ties."""
        name = (source_name or "").strip()
        if not name:
            return MatchResult(source_name, None, 0.0, METHOD_UNMATCHED)

        alias_hit = self.aliases.lookup(source, name, source_id)
        if alias_hit is not None and alias_hit in self._by_id:
            return MatchResult(name, alias_hit, 1.0, METHOD_ALIAS, (self._candidate(alias_hit),))

        # `Last, First` is common enough in exports to be worth a second spelling; everything
        # below sees both, and the result still reports the name as the source wrote it.
        spellings = [name]
        uninverted = uninvert_name(name)
        if uninverted:
            spellings.append(uninverted)

        for spelling in spellings:
            for method, bucket in (
                (METHOD_EXACT, self._by_exact.get(spelling)),
                (METHOD_NORMALIZED, self._by_normalized.get(normalize_name(spelling))),
                # Same letters, different spacing — still a certainty, not a guess.
                (METHOD_NORMALIZED, self._by_compact.get(compact_name(spelling))),
            ):
                if not bucket:
                    continue
                chosen, remaining = self._disambiguate(bucket, team, positions)
                candidates = tuple(self._candidate(player.player_id, 1.0) for player in remaining)
                if chosen is None:
                    return MatchResult(name, None, 0.0, METHOD_AMBIGUOUS, candidates)
                return MatchResult(name, chosen.player_id, 1.0, method, candidates)

        best = MatchResult(name, None, 0.0, METHOD_UNMATCHED)
        for spelling in spellings:
            result = self._fuzzy(spelling, team, positions)
            if result.matched:
                return MatchResult(
                    name, result.player_id, result.confidence, result.method, result.candidates
                )
            if result.confidence > best.confidence or result.method == METHOD_AMBIGUOUS:
                best = MatchResult(name, None, result.confidence, result.method, result.candidates)
        return best

    def _fuzzy(self, name: str, team: str | None, positions: Sequence[str] | None) -> MatchResult:
        """Best normalized-name similarity, accepted only if clear *and* clearly the best."""
        tokens = name_tokens(name)
        normalized = " ".join(tokens)
        surname = tokens[-1] if tokens else ""

        scored = sorted(
            (
                (self.scorer(normalized, self._normalized[player.player_id]), player)
                for player in self._surname_pool(surname)
            ),
            key=lambda pair: -pair[0],
        )
        candidates = tuple(self._candidate(player.player_id, score) for score, player in scored[:5])
        if not scored or scored[0][0] < self.threshold:
            best = scored[0][0] if scored else 0.0
            return MatchResult(name, None, best, METHOD_UNMATCHED, candidates)

        best_score = scored[0][0]
        tied = [player for score, player in scored if best_score - score <= self.margin]
        chosen, remaining = self._disambiguate(tied, team, positions)
        if chosen is None:
            return MatchResult(
                name,
                None,
                best_score,
                METHOD_AMBIGUOUS,
                tuple(self._candidate(player.player_id) for player in remaining),
            )
        return MatchResult(name, chosen.player_id, best_score, METHOD_FUZZY, candidates)

    def _surname_pool(self, surname: str) -> list[CanonicalPlayer]:
        """Players worth scoring against this name: same surname, or near enough to one.

        Requiring the surname to line up is a cheap, strong guard as well as an optimization.
        "Jalen Green" and "Jalen Brunson" score 0.71 on the full name — uncomfortably close to
        the threshold for two people who share nothing that matters. Near-miss surnames still
        get in ("Wembanyama" vs "Wembanyma"), so a typo in the surname is still recoverable.
        """
        if not surname:
            return []
        pool = list(self._by_surname.get(surname, ()))
        seen = {player.player_id for player in pool}
        for other, players in self._by_surname.items():
            if other == surname or self.scorer(surname, other) < self.threshold:
                continue
            pool.extend(player for player in players if player.player_id not in seen)
            seen.update(player.player_id for player in players)
        return pool

    def _disambiguate(
        self,
        players: Sequence[CanonicalPlayer],
        team: str | None,
        positions: Sequence[str] | None,
    ) -> tuple[CanonicalPlayer | None, list[CanonicalPlayer]]:
        """Narrow several equally-named players to one, using whatever hints the caller has.

        Team first (it is nearly always decisive and rarely wrong), then position, then
        active status. Returns `(None, survivors)` when the hints run out with more than one
        left — the caller reports that as ambiguous rather than picking.
        """
        remaining = list(players)
        if len(remaining) <= 1:
            return (remaining[0] if remaining else None), remaining

        if team:
            wanted = team.strip().upper()
            narrowed = [player for player in remaining if (player.nba_team or "").upper() == wanted]
            if len(narrowed) == 1:
                return narrowed[0], remaining
            if narrowed:
                remaining = narrowed

        if positions:
            wanted_positions = {position.strip().upper() for position in positions}
            narrowed = [
                player for player in remaining if wanted_positions & set(player.positions or ())
            ]
            if len(narrowed) == 1:
                return narrowed[0], remaining
            if narrowed:
                remaining = narrowed

        active = [player for player in remaining if player.is_active]
        if len(active) == 1:
            return active[0], remaining

        return None, remaining

    def _candidate(self, player_id: int, score: float = 1.0) -> MatchCandidate:
        player = self._by_id[player_id]
        return MatchCandidate(player.player_id, player.full_name, player.nba_team, score)
