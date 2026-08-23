# Draft Tool — Feature Specification (Imminent Build)

_Last updated: 2026-08-21_

**Scope:** "Imminent" = everything needed for the dynasty-startup snake draft (~2 weeks out).
In-season features (games-maximizer, trade analyzer, league/waiver feed, live odds) are
**designed-for** here but **built after** the draft. This doc is the "exactly what we're building"
reference; open decisions are flagged inline and collected in §12.

---

## 0. Data-sourcing reality (read first — it shapes the infrastructure)

- **No clean free public API** for consensus ADP or expert projections in fantasy basketball.
  FantasyPros' API is partner/enterprise-gated; Hashtag Basketball, Basketball Monster, RotoWire,
  CBS are web-only.
- **ESPN ADP + projections ARE free** via the ESPN API we already use.
- **Sportsbook props come in two flavors:**
  - _Game-by-game props_ (points/reb/ast/etc. for upcoming games) — well covered by APIs
    (BALLDONTLIE, The Odds API), but only meaningful once the season starts. Power the
    games-maximizer in-season.
  - _Season-long player props_ (season point totals, PPG over/unders, stat leaders, awards) —
    these **exist on the books preseason and are valuable for draft value**, but the affordable
    odds APIs **do not expose them** (game-props only). So we ingest them via **manual/paste
    import** (low volume, stable lines, one-time preseason entry for key players) and de-vig them
    into a **market-implied season projection**.
- Almost all published ADP is **redraft**, not dynasty-startup → we treat consensus ADP as a
  redraft signal and apply a **dynasty (age/longevity) adjustment**.

**→ Architecture is import-first.** A generic ingestion pipeline that (a) pulls ESPN automatically
and (b) accepts **CSV/paste imports** from any source, matched to canonical players. Robust
regardless of source availability, and legal (no scraping).

---

## 1. Canonical player identity (foundational — everything references it)

- `Player` table keyed by **ESPN player_id**: name, NBA team, positions, birthdate/age, status.
- **Alias / name-matching layer** maps every external source (ADP, projections, props) to a
  canonical player via fuzzy match + manual alias overrides. Every ranking/projection row stores
  a resolved `player_id`. This is the piece that makes multi-source data actually line up.

## 2. ESPN sync & league settings

- Cookie auth (`espn_s2`/`SWID`). Pull: player pool, ESPN projections, ESPN ADP, **draft settings**
  (team count, rounds, our draft slot, snake order), and **custom scoring coefficients** (`mSettings`).
- Cached; manual "Sync now" + daily job. Faster polling reserved for draft day.

## 3. Data ingestion pipeline

- **ESPN adapter** — automatic.
- **CSV import** — upload or paste → column-mapping step → rows matched to canonical players →
  stored as a named `DataSource` (type: adp | projection | ranking | market_line). Handles
  FantasyPros / Hashtag / any export, imported ranking lists, and **manually-entered season-long
  sportsbook lines** (season totals / PPG over-unders → de-vigged to a market-implied season
  projection). Unmatched names surfaced for one-click manual resolution.

## 4. Projection & value engine

- For each projection source: projected per-game raw stats → **× our custom scoring coefficients**
  → projected fantasy points (per-game **and** season, using projected games played).
- Stored per source; blendable. Pluggable `ProjectionSource` interface (ESPN, imported CSV now;
  sportsbook props + custom model later).
- **Two value horizons per player (both always computed):**
  - _Current-year value_ — projected fantasy points this season under our scoring; win-now,
    age-agnostic.
  - _Dynasty value_ — the same projection run through an **age/longevity curve** (youth rewarded,
    aging players discounted) for long-term worth.
  - Every ranking set (§5) carries a **Current-Year ⇄ Dynasty toggle**; tiers, best-available, and
    the draft plan all follow the selected horizon. Since this is a dynasty *startup*, both lenses
    matter on the same board.

## 5. Ranking sets (three, and the model is extensible to more)

Each ranking set is a first-class object: an ordered list of players with a value, a rank, and
optional tiers. All three render side-by-side on the board.

### 5a. Consensus ADP
- Aggregate ADP across all available sources (ESPN auto + any imported), **blended** (mean or
  median, configurable; optional drop-high/low). Redraft-based; a **dynasty-adjusted** variant
  applies the age/longevity factor.

### 5b. Projected-value ranking (the "sportsbook" set)
- Ranked by projected fantasy points under our scoring.
- **Preseason (draft):** projection systems (ESPN + imported) **plus manually-imported
  season-long sportsbook lines** (season totals / PPG over-unders), de-vigged into a
  market-implied season projection. A market line, where one exists, is a strong signal for
  draft value.
- **In-season:** game-by-game sportsbook props (de-vigged × our scoring, via API) drive the
  games-maximizer; season-long value stays projection/model-driven, anchored by any market lines.

### 5c. Personal ranking (flagship draft tool)
- **Composite model score** = weighted blend of signals per player:
  projected value, consensus ADP (inverted), dynasty age factor, positional scarcity, (extensible).
  Weights are adjustable (sliders).
- **Two weight profiles — Current-Year and Dynasty** (per the §4 horizon toggle). Current-year
  profile turns the age/longevity weight off; dynasty profile turns it on. Each independently
  tunable and learnable.
- **Manual override layer:** drag-to-reorder, pin, exclude, notes, **target flags**. Overrides
  persist and win over the model.
- **"Training" = learn weights from our manual ordering.** A preference/rank regression fits the
  blend weights to match how we've hand-ranked players, then generalizes our taste to the
  players we haven't touched. Re-fittable on demand ("learn from my edits").
- **Versioned snapshots** — save/label/revert/compare ranking versions.

## 6. Tiers

- **Manual tier breaks** (drag dividers) **+ auto-tiering** by score-gap clustering; toggle per
  ranking set. Tiers are what turn a flat list into a draft plan.

## 7. Draft plan generator

- Inputs: team count, snake order, **our pick slots**, roster construction / positional needs.
- **Best-available projection** at each of our upcoming picks via **Monte Carlo** using ADP
  distributions (estimate who's likely gone by our pick).
- Round-by-round **target lists**, positional-need tracking, and **contingencies** (if target gone
  → ranked fallbacks). Uses our personal ranking + tiers.

## 8. Live draft mode

- Track picks: **auto-follow** via ESPN `mDraftDetail` polling on draft day, with **manual entry**
  fallback.
- Remove drafted players, recompute **best-available across all three sets**, highlight **our turn**,
  and update the plan live.

## 9. UI (Next.js)

- **Draft board:** sortable/filterable table; columns for each ranking set + our personal rank/tier;
  **drag-to-reorder** personal ranking; tier dividers; target flags; player detail drawer.
- **Draft plan panel:** upcoming picks, projected best-available, targets, positional needs.
- **Live pick feed:** picks as they happen; our-turn alert.

## 10. Data model additions (beyond the base plan)

`Player`, `PlayerAlias`, `DataSource`, `ProjectionLine`, `RankingSet`, `RankingEntry`, `Tier`,
`PersonalModel` (weights + per-player features), `Override`, `RankingVersion`,
`DraftState`, `DraftPick`, `DraftPlan`.

## 11. In-season (designed-for, built after the draft)

Games-maximizer / streaming planner, trade analyzer, league mirror + transaction/waiver feed,
live sportsbook-props layer, custom projection model (nba_api + aging curves). The ingestion,
projection, scoring, and player-identity layers above are built to serve these without rework.

---

## 12. Decisions needed (to finalize before scaffolding)

1. **External data ingestion** — CSV/paste import first (recommended), build scrapers, or ESPN-only to start?
2. **Personal-ranking model** — weighted blend + learn-from-edits (recommended), manual-drag-only first, or something else?
3. **Tiers** — manual, auto, or both (recommended)?
4. **Draft plan depth** — Monte Carlo best-available simulation (recommended) or simple target lists first?
5. **Co-managers** — one shared personal ranking you both edit (assumed), and one "driver" runs the tool live during the draft?
6. **Projection inputs for the draft** — ESPN only, or will you also import a source like FantasyPros/Hashtag via CSV?
