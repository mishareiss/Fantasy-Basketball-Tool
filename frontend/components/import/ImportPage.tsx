"use client";

import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  api,
  type ImportKindInfo,
  type ImportResponse,
  type ImportRowOutcome,
  type MatchCandidate,
} from "@/lib/api";
import {
  EMPTY_FORM,
  buildRequest,
  formKey,
  validate,
  type ImportForm,
} from "@/lib/importing";
import { ImportConfig } from "./ImportConfig";
import { ImportPreview } from "./ImportPreview";
import { ImportFailed, ImportLoading, KindsUnavailable } from "./ImportStates";
import { KindPicker } from "./KindPicker";
import { TableInput } from "./TableInput";

/**
 * The importer: a browser front end for `POST /import/{kind}`, and nothing else.
 *
 * Every decision here follows from the pipeline being two-phase already (app/ingest). The
 * page never writes on its own: it previews, shows the whole row-by-row outcome, lets you
 * resolve what needs resolving, and only then commits — the same two calls the CLI makes,
 * with the same body. There is no new endpoint and no upload: a dropped file is read by the
 * browser into the textarea, so what is sent is always the text you can see.
 *
 * Two guards are worth the state they cost:
 *
 * * a **stale preview can't be committed**. The preview is keyed by the request that produced
 *   it, and changing any field invalidates it. A ranking commit replaces a whole set, so
 *   committing a preview of the previous form is expensive in a way an ADP row never is.
 * * **resolving re-previews**. Recording an alias changes what the *next* run would do, so
 *   the receipt you commit is always one you have actually seen.
 */

type Kinds =
  | { status: "loading" }
  | { status: "ready"; kinds: ImportKindInfo[] }
  | { status: "error"; error: ApiError };

type Run =
  | { status: "loading"; label: string }
  | { status: "ready"; response: ImportResponse; key: string }
  | { status: "error"; error: ApiError };

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(String(caught));
}

const BUTTON =
  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

export function ImportPage() {
  const [kinds, setKinds] = useState<Kinds>({ status: "loading" });
  const [form, setForm] = useState<ImportForm>(EMPTY_FORM);
  const [run, setRun] = useState<Run | null>(null);
  const [resolving, setResolving] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .importKinds()
      .then((loaded) => {
        if (cancelled) return;
        setKinds({ status: "ready", kinds: loaded });
        // Never leave the form pointed at a kind the backend doesn't handle — the default is
        // only a guess about what is registered.
        setForm((current) =>
          loaded.some((kind) => kind.implemented && kind.kind === current.kind)
            ? current
            : { ...current, kind: loaded.find((kind) => kind.implemented)?.kind ?? current.kind },
        );
      })
      .catch((caught: unknown) => {
        if (!cancelled) setKinds({ status: "error", error: asApiError(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const problem = validate(form);
  // Memoized because the key stringifies the pasted table, which can be a few hundred
  // kilobytes; it only has to change when the form does, not when a re-render happens.
  const key = useMemo(() => formKey(form), [form]);
  const preview = run?.status === "ready" ? run.response : null;
  const stale = run?.status === "ready" && run.key !== key;
  const committed = preview !== null && !preview.dry_run;

  async function send(mode: "preview" | "commit", current: ImportForm = form) {
    setRun({
      status: "loading",
      label: mode === "commit" ? "Writing…" : "Parsing and matching…",
    });
    const body = buildRequest(current);
    try {
      const response =
        mode === "commit"
          ? await api.importCommit(current.kind, body)
          : await api.importPreview(current.kind, body);
      setRun({ status: "ready", response, key: formKey(current) });
    } catch (caught: unknown) {
      setRun({ status: "error", error: asApiError(caught) });
    }
  }

  /**
   * "That one" — record the alias, then re-run the preview so the row moves.
   *
   * The alias is recorded against the *import's* source, not the manual one: the point is
   * that this source calls our player that, which is what makes the next import of any file
   * from them resolve the same name instantly.
   */
  async function resolve(row: ImportRowOutcome, candidate: MatchCandidate) {
    setResolving(row.line);
    try {
      await api.addPlayerAlias(candidate.player_id, {
        source: form.source.trim(),
        source_name: row.source_name,
      });
    } catch (caught: unknown) {
      setRun({ status: "error", error: asApiError(caught) });
      setResolving(null);
      return;
    }
    await send("preview");
    setResolving(null);
  }

  const update = (next: Partial<ImportForm>) => setForm((current) => ({ ...current, ...next }));
  const lines = form.text.trim() ? form.text.trim().split("\n").length : 0;

  if (kinds.status === "loading") return <ImportLoading label="Loading import kinds…" />;
  if (kinds.status === "error") return <KindsUnavailable error={kinds.error} />;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">Import</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Paste or drop someone else&rsquo;s table and match it to our players. Previewing
          writes nothing; committing writes the rows plus an alias per accepted name, so the
          next import of that source resolves instantly.
        </p>
      </header>

      <KindPicker
        kinds={kinds.kinds}
        value={form.kind}
        onChange={(kind) => update({ kind })}
      />

      <TableInput
        value={form.text}
        onChange={(text) => update({ text })}
        rowsHint={lines ? `${lines} line${lines === 1 ? "" : "s"}` : undefined}
      />

      <ImportConfig form={form} onChange={update} />

      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={problem !== null || run?.status === "loading"}
            onClick={() => void send("preview")}
            className={`${BUTTON} bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300`}
          >
            Preview
          </button>
          <button
            type="button"
            disabled={
              problem !== null ||
              run?.status === "loading" ||
              preview === null ||
              stale ||
              committed
            }
            onClick={() => void send("commit")}
            title={
              preview === null
                ? "Preview it first — a commit writes, and a ranking commit replaces a whole set"
                : undefined
            }
            className={`${BUTTON} border border-zinc-300 text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900`}
          >
            Commit
          </button>
          {problem ? <span className="text-xs text-amber-600">{problem}</span> : null}
          {!problem && stale ? (
            <span className="text-xs text-amber-600">
              Something changed since this preview — preview again before committing.
            </span>
          ) : null}
          {!problem && !stale && committed ? (
            <span className="text-xs text-zinc-500">
              Committed. Preview again to import another table.
            </span>
          ) : null}
        </div>
      </section>

      {run?.status === "loading" ? <ImportLoading label={run.label} /> : null}
      {run?.status === "error" ? <ImportFailed error={run.error} /> : null}
      {run?.status === "ready" ? (
        <ImportPreview response={run.response} onResolve={resolve} resolving={resolving} />
      ) : null}
    </div>
  );
}
