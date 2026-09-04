import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { ImportPage } from "@/components/import/ImportPage";
import {
  RESOLVED_ROWS,
  aliasResponse,
  importKinds,
  importResponse,
} from "./fixtures";

/**
 * Component tests for the importer.
 *
 * The api client is mocked wholesale — nothing here touches the network — but `ApiError` is
 * kept real, because the failure panels are chosen by the status code on it and a fake error
 * class would let those branches rot untested. Same arrangement as the board's tests.
 *
 * The one thing worth stating up front: `importPreview` and `importCommit` are separate
 * mocked functions here *because they are separate functions in the client*, which is the
 * whole reason `dry_run` can't be got wrong by a stray boolean.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      importKinds: vi.fn(),
      importPreview: vi.fn(),
      importCommit: vi.fn(),
      addPlayerAlias: vi.fn(),
    },
  };
});

const kinds = vi.mocked(api.importKinds);
const preview = vi.mocked(api.importPreview);
const commit = vi.mocked(api.importCommit);
const addAlias = vi.mocked(api.addPlayerAlias);

const CSV = "PLAYER,TEAM,Avg Pick\nGilgeous-Alexander; Shai,OKC,1.2\n";

beforeEach(() => {
  kinds.mockResolvedValue(importKinds());
  preview.mockResolvedValue(importResponse());
  commit.mockResolvedValue(importResponse({ dry_run: false, rows_created: 3 }));
  addAlias.mockResolvedValue(aliasResponse());
});

afterEach(() => {
  vi.clearAllMocks();
});

/** Fill in the two fields nothing can be sent without, and wait for the kinds to arrive. */
async function fillIn(user: ReturnType<typeof userEvent.setup>, text = CSV) {
  await screen.findByRole("button", { name: /adp/ });
  await user.type(screen.getByLabelText("Source"), "hashtag");
  // `type` on a 300-row paste is unusably slow, and a paste is what this really is.
  await user.click(screen.getByLabelText("Table"));
  await user.paste(text);
}

function kindButton(name: string | RegExp) {
  return within(screen.getByRole("group", { name: "Kind" })).getByRole("button", { name });
}

describe("the kind picker", () => {
  it("renders the kinds the backend says it has", async () => {
    render(<ImportPage />);

    expect(await screen.findByRole("button", { name: /adp/ })).toBeTruthy();
    expect(kindButton(/projection/)).toBeTruthy();
    expect(kindButton(/ranking/)).toBeTruthy();
  });

  it("shows a planned kind as disabled rather than hiding it", async () => {
    render(<ImportPage />);

    const planned = await screen.findByRole("button", { name: /market_line/ });
    expect(planned.hasAttribute("disabled")).toBe(true);
    expect(within(planned).getByText("coming soon")).toBeTruthy();
  });

  it("hints at the columns and aliases the chosen kind looks for", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);

    await screen.findByRole("button", { name: /adp/ });
    expect(screen.getByText("adp, avg pick, average pick, rank")).toBeTruthy();
    expect(screen.getByText("required")).toBeTruthy();

    await user.click(kindButton(/ranking/));
    expect(screen.getByText("rank, rk, #")).toBeTruthy();
  });

  it("falls back to a kind the backend actually implements", async () => {
    // A backend that dropped `adp` shouldn't leave the form pointed at it.
    kinds.mockResolvedValue(importKinds().filter((kind) => kind.kind !== "adp"));
    render(<ImportPage />);

    const chosen = await screen.findByRole("button", { name: /projection/ });
    expect(chosen.getAttribute("aria-pressed")).toBe("true");
  });

  it("says so, and shows no form, when the kind listing itself fails", async () => {
    kinds.mockRejectedValue(new ApiError("/import/kinds responded 500", 500, "boom"));
    render(<ImportPage />);

    expect(await screen.findByText("Can’t list what’s importable")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Kind" })).toBeNull();
  });
});

describe("previewing", () => {
  it("won't send a table with no source, or a source with no table", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await screen.findByRole("button", { name: /adp/ });

    const button = screen.getByRole("button", { name: "Preview" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/Paste a table/)).toBeTruthy();

    await user.click(screen.getByLabelText("Table"));
    await user.paste(CSV);
    expect(screen.getByText(/A source is required/)).toBeTruthy();

    await user.type(screen.getByLabelText("Source"), "hashtag");
    expect(button.hasAttribute("disabled")).toBe(false);
  });

  it("sends the pasted table and renders the counts, the columns and the rows", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);

    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText("Preview — nothing written yet")).toBeTruthy();
    expect(preview).toHaveBeenCalledWith("adp", {
      source: "hashtag",
      text: CSV,
      season: null,
      delimiter: null,
      options: null,
    });

    // The counters, each read from its own tile rather than from a loose number.
    for (const [label, count] of [
      ["Parsed", "3"],
      ["Matched", "1"],
      ["Review", "1"],
      ["Unmatched", "1"],
    ]) {
      const tile = screen.getByText(label).closest("div") as HTMLElement;
      expect(within(tile).getByText(count)).toBeTruthy();
    }

    // The detected column map, which is the mis-detection nothing else would reveal.
    const columns = screen.getByText("Detected columns").closest("div") as HTMLElement;
    expect(within(columns).getByText("Avg Pick", { exact: false })).toBeTruthy();
    expect(within(columns).getByText("adp")).toBeTruthy();

    // A row per line of the file, with its status and what it resolved to. Scoped to the
    // table: an unmatched name is deliberately shown twice, here and in the manual list.
    const table = screen.getByRole("table");
    const row = within(table).getByText("Victor Wembanyma").closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("review")).toBeTruthy();
    expect(within(table).getByText("Shai Gilgeous-Alexander")).toBeTruthy();
    expect(within(table).getByText("Nikola Topić")).toBeTruthy();
  });

  it("shows the parse in flight, and never two previews at once", async () => {
    const user = userEvent.setup();
    preview.mockImplementation(() => new Promise(() => {}));
    render(<ImportPage />);
    await fillIn(user);

    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(screen.getByRole("status").textContent).toContain("Parsing and matching…");
    expect(screen.getByRole("button", { name: "Preview" }).hasAttribute("disabled")).toBe(true);
  });

  it("puts unmatched names with no candidate in their own list", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);

    await user.click(screen.getByRole("button", { name: "Preview" }));

    const manual = await screen.findByText(/Needs a manual match \(1\)/);
    expect(within(manual.parentElement as HTMLElement).getByText(/Nikola Topić/)).toBeTruthy();
  });
});

describe("resolving a review row", () => {
  it("records an alias against the import's source and re-previews", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Victor Wembanyma");

    // Second run of the same file, now that the alias exists.
    preview.mockResolvedValue(importResponse({ rows: RESOLVED_ROWS, aliases_existing: 1 }));
    await user.click(screen.getByRole("button", { name: /Victor Wembanyama/ }));

    expect(addAlias).toHaveBeenCalledWith(5104157, {
      source: "hashtag",
      source_name: "Victor Wembanyma",
    });
    // The row moved: same line, now an alias hit, and no candidates left to pick.
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2));
    const row = within(await screen.findByRole("table"))
      .getByText("Victor Wembanyma")
      .closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("matched")).toBeTruthy();
    expect(within(row).getByText(/alias 1\.00/)).toBeTruthy();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("surfaces the backend's message when the alias itself is refused", async () => {
    const user = userEvent.setup();
    addAlias.mockRejectedValue(new ApiError("responded 404", 404, "No player 5104157"));
    render(<ImportPage />);
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    await user.click(await screen.findByRole("button", { name: /Victor Wembanyama/ }));

    expect(await screen.findByText("No player 5104157")).toBeTruthy();
    expect(preview).toHaveBeenCalledTimes(1);
  });
});

describe("committing", () => {
  it("is refused until the current form has been previewed", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);

    expect(screen.getByRole("button", { name: "Commit" }).hasAttribute("disabled")).toBe(true);

    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Preview — nothing written yet");
    expect(screen.getByRole("button", { name: "Commit" }).hasAttribute("disabled")).toBe(false);

    // Changing anything invalidates the preview: a ranking commit replaces a whole set.
    await user.type(screen.getByLabelText("Source"), "-x");
    expect(screen.getByRole("button", { name: "Commit" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/preview again before committing/)).toBeTruthy();
  });

  it("writes through the commit call and shows the receipt with a way back", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Preview — nothing written yet");

    await user.click(screen.getByRole("button", { name: "Commit" }));

    expect(await screen.findByText("Committed")).toBeTruthy();
    expect(commit).toHaveBeenCalledWith("adp", expect.objectContaining({ source: "hashtag" }));
    expect(preview).toHaveBeenCalledTimes(1);
    const link = screen.getByRole("link", { name: /Back to the board/ });
    expect(link.getAttribute("href")).toBe("/");
  });
});

describe("the per-kind options", () => {
  it("sends a ranking's set name and horizon, and requires the horizon", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);
    await user.click(kindButton(/ranking/));

    // The horizon has no default: a rank-only list has no stats to age-adjust, so guessing
    // would file the board under the wrong lens and look right doing it.
    expect(screen.getByRole("button", { name: "Preview" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/dynasty or a redraft board/)).toBeTruthy();

    await user.type(screen.getByLabelText("Set name"), "Dynasty Top 200");
    await user.selectOptions(screen.getByLabelText("Horizon"), "dynasty");
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(preview).toHaveBeenCalledWith(
      "ranking",
      expect.objectContaining({
        options: { name: "Dynasty Top 200", horizon: "dynasty" },
      }),
    );
  });

  it("sends a projection's basis and offers no horizon", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);
    await user.click(kindButton(/projection/));

    expect(screen.queryByLabelText("Horizon")).toBeNull();
    await user.selectOptions(screen.getByLabelText("Basis"), "season");
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(preview).toHaveBeenCalledWith(
      "projection",
      expect.objectContaining({ options: { basis: "season" } }),
    );
  });

  it("sends no options at all for a kind that takes none", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);

    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(preview).toHaveBeenCalledWith("adp", expect.objectContaining({ options: null }));
  });

  it("passes a typed season and delimiter through, and omits them when left blank", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await fillIn(user);
    await user.type(screen.getByLabelText("Season"), "2027");
    await user.selectOptions(screen.getByLabelText("Delimiter"), "\t");

    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(preview).toHaveBeenCalledWith(
      "adp",
      expect.objectContaining({ season: 2027, delimiter: "\t" }),
    );
  });
});

describe("failures", () => {
  it("shows the backend's own 409 when a projection has nothing to price with", async () => {
    const user = userEvent.setup();
    preview.mockRejectedValue(
      new ApiError("/import/projection responded 409", 409, "No scoring rules loaded"),
    );
    render(<ImportPage />);
    await fillIn(user);
    await user.click(kindButton(/projection/));

    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText("No scoring rules loaded")).toBeTruthy();
    expect(screen.getByText(/make sync/)).toBeTruthy();
  });

  it("explains a 422 as content, not as a broken request", async () => {
    const user = userEvent.setup();
    preview.mockRejectedValue(
      new ApiError("responded 422", 422, "unknown option(s) for a ranking import: ['basis']"),
    );
    render(<ImportPage />);
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText(/unknown option/)).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("names the base URL when nothing answered at all", async () => {
    const user = userEvent.setup();
    preview.mockRejectedValue(new ApiError("Could not reach the API at http://localhost:8000"));
    render(<ImportPage />);
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText("Can’t reach the API")).toBeTruthy();
    expect(screen.getByText(/make backend/)).toBeTruthy();
  });
});

describe("the file picker", () => {
  it("reads a dropped file in the browser and fills the textarea with it", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    await screen.findByRole("button", { name: /adp/ });

    await user.upload(
      screen.getByLabelText("Table file"),
      new File([CSV], "adp.csv", { type: "text/csv" }),
    );

    const textarea = screen.getByLabelText("Table") as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe(CSV));
    // The point of reading it here: the text landed in the box, editable, un-uploaded.
    expect(screen.getByText("adp.csv")).toBeTruthy();
    expect(screen.getByText("2 lines")).toBeTruthy();
  });
});
