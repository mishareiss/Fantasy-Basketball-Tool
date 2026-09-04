"use client";

import { useRef, useState } from "react";

/**
 * Step two: the table itself, pasted or dropped.
 *
 * There is deliberately no upload endpoint. A file dropped here is read **in the browser**
 * with `FileReader` and its text goes into the same textarea a paste would — so both paths
 * hit one endpoint with one body, and the backend never grows a multipart route (which would
 * cost a dependency) to do something the browser already does.
 *
 * The consequence worth knowing: what gets imported is exactly what is in the box. Editing
 * the text after dropping a file is not just allowed, it is the fix for a stray footer row.
 */

/** Read a dropped/picked file as text. utf-8 by default, which is what exports are. */
export function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsText(file);
  });
}

/** The BOM Excel puts at the front of a CSV, which would otherwise glue itself to header one. */
function stripBom(text: string): string {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

export function TableInput({
  value,
  onChange,
  rowsHint,
}: {
  value: string;
  onChange: (text: string) => void;
  /** Shown beside the label: how many lines are in the box right now. */
  rowsHint?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function accept(file: File | undefined | null) {
    if (!file) return;
    setReadError(null);
    try {
      const text = await readFileText(file);
      onChange(stripBom(text));
      setFileName(file.name);
    } catch (caught: unknown) {
      setReadError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          2. Paste or drop the table
        </h2>
        {rowsHint ? <span className="font-mono text-xs text-zinc-500">{rowsHint}</span> : null}
      </div>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void accept(event.dataTransfer.files?.[0]);
        }}
        className={`flex flex-col gap-2 rounded-lg border-2 border-dashed p-2 transition-colors ${
          dragging
            ? "border-sky-500 bg-sky-50 dark:border-sky-400 dark:bg-sky-950/30"
            : "border-zinc-200 dark:border-zinc-800"
        }`}
      >
        <label htmlFor="import-text" className="sr-only">
          Table
        </label>
        <textarea
          id="import-text"
          value={value}
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
          rows={10}
          placeholder={
            "Paste a CSV or a spreadsheet selection here — or drop a file anywhere in this box.\n\n" +
            "RK,PLAYER,TEAM,POS\n1,Shai Gilgeous-Alexander,OKC,PG"
          }
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 font-mono text-xs text-zinc-800 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
        />
        <div className="flex flex-wrap items-center gap-3 px-1 pb-1">
          <input
            ref={inputRef}
            id="import-file"
            type="file"
            aria-label="Table file"
            accept=".csv,.tsv,.txt,text/csv,text/plain,text/tab-separated-values"
            onChange={(event) => void accept(event.target.files?.[0])}
            className="sr-only"
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="rounded-md border border-zinc-300 px-2.5 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
          >
            Choose a file…
          </button>
          {value ? (
            <button
              type="button"
              onClick={() => {
                onChange("");
                setFileName(null);
                setReadError(null);
              }}
              className="text-xs text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
            >
              Clear
            </button>
          ) : null}
          <span className="text-xs text-zinc-500">
            {fileName ? (
              <>
                Read <span className="font-mono">{fileName}</span> in the browser — nothing was
                uploaded.
              </>
            ) : (
              "The file is read here, not uploaded: what imports is what's in the box."
            )}
          </span>
        </div>
      </div>

      {readError ? (
        <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">
          {readError}
        </p>
      ) : null}
    </section>
  );
}
