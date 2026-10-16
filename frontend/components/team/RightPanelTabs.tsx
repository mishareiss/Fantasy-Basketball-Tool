"use client";

/**
 * The right panel's `Waivers | Plan` tabs. Both panels stay mounted (the waiver list keeps its
 * filters and its page while the Plan tab is up); the hidden one is `hidden`.
 *
 * The choice is remembered per browser, guarded like every other stored preference here: a
 * blocked store just means Waivers.
 */

export type RightTab = "waivers" | "plan";

const STORAGE_KEY = "team.rightPanel";

export function rememberedTab(): RightTab {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "plan" ? "plan" : "waivers";
  } catch {
    return "waivers";
  }
}

export function rememberTab(tab: RightTab) {
  try {
    window.localStorage.setItem(STORAGE_KEY, tab);
  } catch {
    // A blocked store just means Waivers next time.
  }
}

const TABS: { tab: RightTab; label: string }[] = [
  { tab: "waivers", label: "Waivers" },
  { tab: "plan", label: "Plan" },
];

export function RightPanelTabs({
  tab,
  onChange,
  planCount,
  waivers,
  plan,
}: {
  tab: RightTab;
  onChange: (tab: RightTab) => void;
  /** Planned moves, for the tab's count. Null until the plan has been read. */
  planCount: number | null;
  waivers: React.ReactNode;
  plan: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2" data-right-panel>
      <div role="tablist" aria-label="Right panel" className="flex gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {TABS.map((t) => {
          const on = tab === t.tab;
          return (
            <button
              key={t.tab}
              type="button"
              role="tab"
              id={`right-tab-${t.tab}`}
              aria-selected={on}
              aria-controls={`right-panel-${t.tab}`}
              onClick={() => onChange(t.tab)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium ${
                on
                  ? "border-sky-600 text-zinc-900 dark:border-sky-400 dark:text-zinc-100"
                  : "border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
              }`}
            >
              {t.label}
              {t.tab === "plan" && planCount !== null && planCount > 0 ? (
                <span className="ml-1 text-xs text-zinc-500 tabular-nums">({planCount})</span>
              ) : null}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id="right-panel-waivers" aria-labelledby="right-tab-waivers" hidden={tab !== "waivers"}>
        {waivers}
      </div>
      <div role="tabpanel" id="right-panel-plan" aria-labelledby="right-tab-plan" hidden={tab !== "plan"}>
        {plan}
      </div>
    </div>
  );
}
