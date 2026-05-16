import type { JSX } from "react";
import { GraphLegend } from "../graph/GraphLegend";
import { useGraphStore } from "../graph/useGraphStore";

export function GraphLegendPanel(): JSX.Element | null {
  const graph = useGraphStore((s) => s.graph);
  const hiddenKinds = useGraphStore((s) => s.hiddenKinds);
  const toggleKind = useGraphStore((s) => s.toggleKind);
  const showAllKinds = useGraphStore((s) => s.showAllKinds);

  return (
    <GraphLegend
      graph={graph}
      hiddenKinds={hiddenKinds}
      onToggleKind={toggleKind}
      onShowAll={showAllKinds}
    />
  );
}
