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
| `lib/api.ts` | Typed client. The response types mirror the backend's pydantic models field for field. |
| `lib/board.ts`, `lib/format.ts` | Horizon vocabulary, and number formatting for a table meant to be scanned. |
| `__tests__/` | Vitest + React Testing Library. |

## Conventions

This version of Next has breaking changes from what a model was trained on — read the guide in
`node_modules/next/dist/docs/` before writing code here. See [AGENTS.md](AGENTS.md).
