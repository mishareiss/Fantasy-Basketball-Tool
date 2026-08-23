# Fantasy Basketball Dynasty Tool — Project Plan

_Last updated: 2026-08-21_

A private tool for two co-managers to gain an edge in an **ESPN** fantasy basketball
**dynasty** league with **H2H points** scoring and a **custom scoring formula**.

---

## 1. Goals

Ranked by current priority:

1. **Draft prep (URGENT).** A dynasty-startup snake draft board ranked under our custom
   scoring, ready for a draft ~2 weeks out (target: early September 2026).
2. **Weekly games-maximizer / streaming planner.** Day-by-day waiver add/drop plan to
   max out games played each week — a top edge in H2H points.
3. **Player valuation + trade analyzer.** Value every player under our scoring; evaluate trades.
4. **League mirror + transaction feed.** See all rosters, standings, and follow the waiver wire.

Audience: just the two co-managers. Not public. Runs locally to start.

---

## 2. What's possible with ESPN (capability findings)

ESPN has **no official API**, but a stable unofficial v3 JSON API exists
(`https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/<YEAR>/segments/0/leagues/<ID>`),
wrapped by the mature Python library [`espn-api`](https://github.com/cwendt94/espn-api)
(supports basketball).

**Auth (private league):** copy two browser cookies — `espn_s2` and `SWID` — and send them
with requests. No OAuth. They belong to your own account for your own league.

**What we can pull:**

| Data | Source | Notes |
|------|--------|-------|
| Rosters (all teams) | `league.teams[].roster` | Refreshes on each sync |
| Box scores / matchups | `league.box_scores()` | Player fantasy pts **already scored under our custom formula** |
| Transactions (add/drop/waiver/trade) | `recent_activity`, `mTransactions2` | Only **completed** transactions, not pending claims |
| Free agents | `league.free_agents()` | Filter by position; includes ESPN projections |
| Draft results / live picks | `mDraftDetail` (`league.draft`) | Enables live draft-following |
| **Custom scoring settings** | `mSettings` | Exposes per-stat point values — the key to valuing by *our* scoring |
| ESPN player projections | `kona_player_info` | Baseline projections to start |
| NBA schedule (games/day) | `nba_api` or ESPN schedule endpoint | Needed for the games-maximizer |
| Sportsbook player props | BALLDONTLIE (or The Odds API) | Points/reb/ast/3s/stl/blk lines from DK, FanDuel, etc. — sharpest short-term projection |

**Limitations / risks:**

- Unofficial API — endpoints can change without notice (they did in 2024). Low but non-zero maintenance.
- `espn_s2`/`SWID` cookies expire periodically (~yearly, sometimes sooner) → need a clean re-auth (paste new cookies) path.
- No webhooks/push → "live" = **polling**, not real-time.
- Only completed transactions visible (can't see other managers' pending waiver claims).
- Undocumented rate limits → sync on a schedule, don't hammer.

---

## 3. Custom scoring approach

Because `mSettings` exposes the exact per-stat point values, **all valuations use our
league's scoring, not ESPN standard.** Actual game results already come back pre-scored by
ESPN under our formula. The only place we apply the formula ourselves is **projections**:

```
projected_fantasy_pts = Σ (projected_stat_i × our_scoring_coefficient_i)
```

The scoring coefficients are loaded once from `mSettings` and cached; a re-sync picks up any
mid-season scoring changes.

---

## 4. Architecture & stack

- **Backend:** Python + **FastAPI**. Wraps `espn-api`, owns sync, scoring, valuation,
  projections, and the optimizer. Exposes a REST API (`/players`, `/draft`, `/trade`, `/waiver-plan`, ...).
- **Frontend:** **Next.js** (React + TypeScript, Tailwind). All UI/board/analysis views.
- **Database:** **Postgres** via **SQLAlchemy 2.0** ORM + **Alembic** migrations, run locally via
  **Docker Compose** for dev. (Chosen over SQLite so a shared hosted version later needs no DB
  migration.)
- **Scheduling:** manual "Sync now" endpoint + one daily job. Faster polling only on draft day
  and around waiver processing.
- **Projections:** pluggable `ProjectionSource` interface with multiple sources blended by horizon:
  - _ESPN projections_ — baseline to start, re-scored under our formula.
  - _Sportsbook props_ (BALLDONTLIE or The Odds API) — de-vig each player-prop line → per-stat
    projection → × our scoring coefficients = **market-implied projected fantasy pts**. Sharpest
    for **game-by-game** (drives the games-maximizer).
  - _Custom model_ (nba_api raw stats + aging curves) — built early; primary for **season-long**.
  - Blend rule: game-by-game weights props heavily; season-long weights the model, anchored by
    props near-term. Season-long prop lines are sparsely covered by APIs, so we **derive** them by
    extrapolating per-game rates across the remaining schedule rather than reading them directly.
  - _Sourcing note:_ never scrape FanDuel/DraftKings directly (no public API, brittle, against ToS);
    always go through an aggregator API. BALLDONTLIE is preferred since one integration gives both
    raw stats and props.

### Proposed repo structure

```
Fantasy-Basketball-Tool/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI entrypoint
│   │   ├── api/               # route handlers
│   │   ├── espn/              # espn-api client + auth (cookies)
│   │   ├── scoring/           # custom scoring formula from mSettings
│   │   ├── projections/       # ESPN source + pluggable custom model
│   │   ├── valuation/         # player value engine (dynasty-aware)
│   │   ├── draft/             # draft board + live pick following
│   │   ├── waivers/           # weekly games-maximizer / streaming optimizer
│   │   ├── db/                # SQLAlchemy models + session
│   │   └── config.py          # settings, secrets from .env
│   ├── alembic/               # migrations
│   ├── tests/
│   └── requirements.txt
├── frontend/                  # Next.js app (own .gitignore)
│   ├── app/
│   ├── components/
│   └── package.json
├── docs/
│   └── PLAN.md                # this file
├── .env.example               # ESPN_S2, SWID, LEAGUE_ID, SEASON (real .env gitignored)
├── .gitignore                 # Python + Node + data/secrets
└── README.md
```

**Secrets:** `espn_s2`, `SWID`, `LEAGUE_ID`, `SEASON` live in a gitignored `.env`
(`.env.example` committed as a template). Never commit cookies.

---

## 5. Data model (initial sketch)

- **Player** — id, name, nba_team, positions, age, injury status.
- **PlayerStatLine** — player, season/period, projected vs actual stats, computed fantasy pts.
- **Valuation** — player, as-of date, value (per-game + total), dynasty-adjusted value, tier.
- **Team** (fantasy) — id, name, manager, roster snapshot.
- **RosterSnapshot** / **Transaction** — history over time (dynasty trend tracking).
- **DraftPick** — draft, round, pick, team, player (populated live during draft).
- **LeagueSettings** — scoring coefficients, roster slots, weekly acquisition limit.

History tables are why we use a real DB: dynasty value is about trends over time.

---

## 6. Feature roadmap

### Phase 0 — Foundation (must exist first)
Auth via cookies, sync layer, load custom scoring settings, DB + migrations, base API + Next app shell.

### Phase 1 — Draft board MVP  ← **current sprint**
- Pull full NBA player pool + ESPN projections; re-score under custom formula.
- **Dynasty-aware value:** per-game projected fantasy pts, adjusted by an age/longevity factor
  (younger = uplift, older = discount) — transparent and tunable.
- Tiers + positional context; sortable/filterable board.
- Mark players drafted; **live-follow** picks via `mDraftDetail` (poll during draft),
  manual mark-off as fallback.

### Phase 2 — Weekly games-maximizer / streaming planner
- Pull NBA schedule (games per team per day) + current roster + free agents.
- Rank candidates by **market-implied projected fantasy pts** (de-vigged sportsbook props ×
  our scoring), falling back to model/ESPN projections when no prop line exists.
- Optimize a **day-by-day add/drop plan** to fill every lineup slot with a player who plays
  that day, maximizing total games (and projected points), subject to the **weekly acquisition limit**.
- Output: "today, drop X for Y" plan for the week.

### Phase 2.5 — Betting-odds projections layer
- Integrate the sportsbook-props source (BALLDONTLIE preferred).
- Surface, per player: game-by-game prop-implied projection and a derived season-long projection.
- Feeds the games-maximizer, valuations, and draft board.

### Phase 3 — Valuation + trade analyzer
- Full rest-of-season + dynasty valuations for all rostered players.
- Trade evaluator: paste a proposed trade → value delta per side (win-now vs long-term).

### Phase 4 — League mirror + transaction feed
- All rosters, standings, and a running log of adds/drops/claims/trades.
- Free-agent finder ("best available to target").

### Later
- Custom projection model (nba_api + aging curves) fully replacing/blending ESPN's.
- Injury/waiver alerts; value-trend charts; Postgres + shared hosting if desired.

---

## 7. Two-week draft sprint (target: draft ~early Sept 2026)

- **Days 1–2:** Repo scaffold (backend + frontend), `.env`/secrets, `.gitignore` for both stacks,
  ESPN auth working end-to-end (pull our real league + scoring settings).
- **Days 3–5:** Player pool + ESPN projections → custom-scoring valuations + dynasty age
  adjustment → ranked, tiered board **data** (the part that actually wins the draft).
- **Days 6–8:** Next.js draft board UI — sortable/filterable table, tiers, mark-drafted.
- **Days 9–10:** Live pick-following (poll `mDraftDetail`), manual fallback, best-available updates.
- **Days 11–12:** Polish, test with a mock draft, tune the dynasty weighting.
- **Buffer:** remaining days for fixes. **Fallback guarantee:** even if the UI slips, the ranked
  board data is usable standalone on draft day.

---

## 8. Open questions

- Exact custom scoring values — will read live from `mSettings` once cookies are in.
- League size, roster slots, weekly acquisition limit — read from settings.
- Dynasty weighting: how aggressively to favor youth (tunable; will calibrate together).
- Hosting: local for now (Postgres via Docker Compose); shared host later if we want remote access for both managers.
- Odds API choice + cost: BALLDONTLIE (stats + props in one) vs The Odds API (free tier ~500 req/mo).
  Live odds are typically a paid tier — decide budget before wiring the props source in.
