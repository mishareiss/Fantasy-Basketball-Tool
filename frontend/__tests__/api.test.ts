import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL, ApiError, api } from "@/lib/api";
import { DEFAULT_CONTROLS } from "@/components/board/BoardView";
import { parseControls, toQuery } from "@/components/board/BoardPage";

/**
 * The client's half of the contract: which URL each call builds, and what an error becomes.
 *
 * `fetch` is stubbed rather than the module — this is the one place where the point IS the
 * request that goes out.
 */

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue(jsonResponse({}));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function requestedUrl(): string {
  return String(fetchMock.mock.calls[0][0]);
}

describe("api.board", () => {
  it("omits parameters the caller left out, so the backend's defaults apply", async () => {
    await api.board({ horizon: "dynasty", position: null, limit: 100, tiers: "auto" });

    const url = new URL(requestedUrl());
    expect(url.origin + url.pathname).toBe(`${API_BASE_URL}/players/board`);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      horizon: "dynasty",
      limit: "100",
      tiers: "auto",
    });
  });

  it("passes every board parameter the endpoint accepts", async () => {
    await api.board({
      horizon: "current_year",
      position: "PG",
      source: "hashtag",
      season: 2027,
      adp_source: "espn",
      adp_season: 2026,
      limit: 25,
      tiers: "off",
    });

    expect(Object.fromEntries(new URL(requestedUrl()).searchParams)).toEqual({
      horizon: "current_year",
      position: "PG",
      source: "hashtag",
      season: "2027",
      adp_source: "espn",
      adp_season: "2026",
      limit: "25",
      tiers: "off",
    });
  });

  it("carries FastAPI's detail up on an error, alongside the status", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "No 'espn' projections stored yet; run `make sync` first." }, 404),
    );

    const error = await api.board().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).detail).toMatch(/make sync/);
  });

  it("turns a network failure into a status-less ApiError naming the base URL", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const error = await api.board().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBeUndefined();
    expect((error as ApiError).message).toContain(API_BASE_URL);
  });
});

describe("api.valuation", () => {
  it("asks for the curve with no parameters", async () => {
    await api.valuationCurve();
    expect(requestedUrl()).toBe(`${API_BASE_URL}/valuation/curve`);
  });

  it("tiers the horizon it is given", async () => {
    await api.valuationTiers("current_year");
    expect(requestedUrl()).toBe(`${API_BASE_URL}/valuation/tiers?horizon=current_year`);
  });
});

describe("board URL state", () => {
  it("round-trips the controls through the query string", () => {
    const controls = { horizon: "current_year", position: "C", limit: 200, tiers: "off" } as const;
    const query = toQuery(controls);

    expect(parseControls(new URLSearchParams(query.replace("/?", "")))).toEqual(controls);
  });

  it("keeps defaults out of the URL, so a shared link stays readable", () => {
    expect(toQuery(DEFAULT_CONTROLS)).toBe("/");
    expect(toQuery({ ...DEFAULT_CONTROLS, position: "PG" })).toBe("/?position=PG");
  });

  it("falls back to the defaults for junk in the query string", () => {
    const parsed = parseControls(
      new URLSearchParams("horizon=vibes&position=QB&limit=9999&tiers=maybe"),
    );
    expect(parsed).toEqual(DEFAULT_CONTROLS);
  });
});
