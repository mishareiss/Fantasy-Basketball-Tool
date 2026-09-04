import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL, ApiError, api } from "@/lib/api";
import { DEFAULT_CONTROLS } from "@/components/board/BoardView";
import { parseControls, toQuery } from "@/components/board/BoardPage";
import { EMPTY_FORM, buildRequest, formKey, validate } from "@/lib/importing";

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


describe("the import calls", () => {
  function requestBody(): unknown {
    return JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
  }

  it("lists the kinds without parameters", async () => {
    await api.importKinds();
    expect(requestedUrl()).toBe(`${API_BASE_URL}/import/kinds`);
  });

  it("previews with dry_run true — the phase that writes nothing", async () => {
    await api.importPreview("ranking", {
      source: "hashtag",
      text: "Rank,Player\n1,Shai Gilgeous-Alexander\n",
      season: 2027,
      delimiter: null,
      options: { name: "Dynasty Top 200", horizon: "dynasty" },
    });

    expect(requestedUrl()).toBe(`${API_BASE_URL}/import/ranking`);
    const init = fetchMock.mock.calls[0][1];
    expect(init?.method).toBe("POST");
    expect(requestBody()).toEqual({
      source: "hashtag",
      text: "Rank,Player\n1,Shai Gilgeous-Alexander\n",
      season: 2027,
      delimiter: null,
      options: { name: "Dynasty Top 200", horizon: "dynasty" },
      dry_run: true,
    });
  });

  it("commits with dry_run false, and nothing else differs", async () => {
    await api.importCommit("adp", { source: "hashtag", text: "a,b\n1,2\n" });

    expect((requestBody() as { dry_run: boolean }).dry_run).toBe(false);
  });

  it("posts an alias to the player it resolves to", async () => {
    await api.addPlayerAlias(5104157, {
      source: "hashtag",
      source_name: "Victor Wembanyma",
    });

    expect(requestedUrl()).toBe(`${API_BASE_URL}/players/5104157/aliases`);
    expect(requestBody()).toEqual({ source: "hashtag", source_name: "Victor Wembanyma" });
  });

  it("carries a 422's detail up the way every other call does", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "a ranking import needs horizon=['dynasty', 'redraft']" }, 422),
    );

    const error = await api
      .importPreview("ranking", { source: "x", text: "y" })
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).detail).toMatch(/horizon/);
  });
});

describe("the import form", () => {
  const form = { ...EMPTY_FORM, source: "hashtag", text: "a,b\n1,2\n" };

  it("leaves out a season and delimiter the user didn't set", () => {
    expect(buildRequest(form)).toEqual({
      source: "hashtag",
      text: "a,b\n1,2\n",
      season: null,
      delimiter: null,
      options: null,
    });
  });

  it("drops a season that isn't a number rather than sending NaN", () => {
    expect(buildRequest({ ...form, season: "twenty" }).season).toBeNull();
    expect(buildRequest({ ...form, season: " 2027 " }).season).toBe(2027);
  });

  it("sends only the options the chosen kind knows about", () => {
    // An option a kind doesn't know is a 422, never a silent default — so `adp` sends none.
    expect(buildRequest({ ...form, basis: "season", name: "x" }).options).toBeNull();
    expect(buildRequest({ ...form, kind: "projection", basis: "season" }).options).toEqual({
      basis: "season",
    });
    expect(
      buildRequest({ ...form, kind: "ranking", horizon: "redraft", name: " Top 200 " }).options,
    ).toEqual({ horizon: "redraft", name: "Top 200" });
  });

  it("omits an unnamed ranking set, so the backend's source-name default applies", () => {
    expect(buildRequest({ ...form, kind: "ranking", horizon: "dynasty" }).options).toEqual({
      horizon: "dynasty",
    });
  });

  it("blocks the three things the backend can't judge better than we can", () => {
    expect(validate({ ...form, text: "  " })).toMatch(/Paste a table/);
    expect(validate({ ...form, source: "" })).toMatch(/source is required/);
    expect(validate({ ...form, kind: "ranking" })).toMatch(/dynasty or a redraft/);
    expect(validate(form)).toBeNull();
    expect(validate({ ...form, kind: "ranking", horizon: "dynasty" })).toBeNull();
  });

  it("keys a preview by the request it came from, so an edited form invalidates it", () => {
    expect(formKey(form)).toBe(formKey({ ...form }));
    expect(formKey(form)).not.toBe(formKey({ ...form, source: "yahoo" }));
    expect(formKey(form)).not.toBe(formKey({ ...form, kind: "ranking", horizon: "dynasty" }));
    // A field this kind doesn't send can't invalidate its preview.
    expect(formKey(form)).toBe(formKey({ ...form, basis: "season" }));
  });
});
