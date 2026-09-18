# Frontend

The Next.js App Router UI. `/` is the **draft board**; `/status` is the backend reachability
check. Everything it shows comes from the FastAPI backend through [`lib/api.ts`](lib/api.ts) —
one place for `fetch`, error shaping (`ApiError`), and the base URL (`NEXT_PUBLIC_API_BASE_URL`,
defaulting to `http://localhost:8000`).

## Running it

```bash
npm run dev     # or `make frontend` from the repo root — http://localhost:3000
```

The board needs a backend with a synced player pool (`make backend`, `make sync`). Without
one it says so: an unreachable API and an empty database get their own explicit states rather
than a blank table.

## Checks

```bash
npm run lint    # eslint (flat config, eslint-config-next)
npm run build   # next build, incl. the TypeScript pass
npm test        # vitest run — component tests, api client mocked, no network
```

All three are what CI runs, in that order. See [`vitest.config.mts`](vitest.config.mts).

## Layout

| Path | What's in it |
| --- | --- |
| `app/` | Routes. `page.tsx` reads the board's query string; `layout.tsx` holds the status strip. |
| `components/board/` | The board: controls, the table and its tier dividers, the loading/error/empty states, and the read-only curve & tiers inspector. |
| `components/import/` | The importer: the kind picker, the paste/drop box (its placeholder shows an example row in the selected kind's format), the per-kind options, and the row-by-row preview. |
| `components/market/` | The market page: the stored lines grouped by player, the add form (which resolves a name through the importer's dry run rather than a second matcher), and in-place edit/delete. |
| `lib/api.ts` | Typed client. The response types mirror the backend's pydantic models field for field. |
| `lib/board.ts`, `lib/format.ts` | Horizon vocabulary, and number formatting for a table meant to be scanned. |
| `lib/importing.ts`, `lib/market.ts` | The pure halves of the two forms: per-kind options and example rows; American odds, and the one-row table a name is resolved against. |
| `__tests__/` | Vitest + React Testing Library. |

## Conventions

This version of Next has breaking changes from what a model was trained on — read the guide in
`node_modules/next/dist/docs/` before writing code here. See [AGENTS.md](AGENTS.md).
