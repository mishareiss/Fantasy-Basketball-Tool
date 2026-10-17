import type { ProjectionSource } from "@/lib/api";
import { projectionTag } from "@/lib/team";

/**
 * The small tag beside a FPTS/G number that isn't this season's ESPN projection: "25-26 actual"
 * (last season's actual line standing in) or "stale proj" (an old projection). Renders
 * nothing for ESPN's own projection and for the actual bases.
 */
export function ProjectionTag({
  source,
  season,
}: {
  source: ProjectionSource | null | undefined;
  season: number | null | undefined;
}) {
  const tag = projectionTag(source, season);
  if (!tag) return null;
  return (
    <span
      title={tag.title}
      data-projection-tag={source ?? ""}
      className="mr-1 rounded bg-amber-100 px-1 py-px align-middle font-sans text-[9px] font-medium whitespace-nowrap text-amber-800 dark:bg-amber-950 dark:text-amber-300"
    >
      {tag.label}
    </span>
  );
}
