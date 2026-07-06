import { GraphCanvas } from "../graph/GraphCanvas";
import { CyclesPanel } from "./CyclesPanel";
import { DiffPanel } from "./DiffPanel";
import { EdgeEvidencePanel } from "./EdgeEvidencePanel";
import { FlameGraphPanel } from "./FlameGraphPanel";
import { GraphLegendPanel } from "./GraphLegendPanel";
import { HeaderChrome } from "./HeaderChrome";
import { NodeInspectorPanel } from "./NodeInspectorPanel";
import { panels } from "./registry";
import { SearchFilterPanel } from "./SearchFilterPanel";
import { SessionLibraryPanel } from "./SessionLibraryPanel";
import { SourceViewer } from "./SourceViewer";
import { StatsPanel } from "./StatsPanel";
import { TimelinePanel } from "./TimelinePanel";
import { ValueInspectorPanel } from "./ValueInspectorPanel";

panels.register({
  slot: "top-bar",
  id: "header-chrome",
  component: HeaderChrome,
  order: 0,
});
panels.register({
  slot: "left-sidebar",
  id: "search-filter",
  component: SearchFilterPanel,
  order: 0,
});
panels.register({
  slot: "floating-overlay",
  id: "graph-canvas",
  component: GraphCanvas,
  order: 0,
});
panels.register({
  slot: "right-sidebar",
  id: "source-viewer",
  component: SourceViewer,
  order: 5,
});
panels.register({
  slot: "right-sidebar",
  id: "node-inspector",
  component: NodeInspectorPanel,
  order: 10,
});
panels.register({
  slot: "right-sidebar",
  id: "value-inspector",
  component: ValueInspectorPanel,
  order: 15,
});
panels.register({
  slot: "right-sidebar",
  id: "graph-legend",
  component: GraphLegendPanel,
  order: 20,
});
panels.register({
  slot: "right-sidebar",
  id: "edge-evidence",
  component: EdgeEvidencePanel,
  order: 25,
});
panels.register({
  slot: "right-sidebar",
  id: "cycles-panel",
  component: CyclesPanel,
  order: 30,
});
panels.register({
  slot: "right-sidebar",
  id: "diff-panel",
  component: DiffPanel,
  order: 35,
});
panels.register({
  slot: "left-sidebar",
  id: "session-library-panel",
  component: SessionLibraryPanel,
  order: 5,
});
panels.register({
  slot: "bottom-dock",
  id: "timeline-panel",
  component: TimelinePanel,
  order: 0,
});
panels.register({
  slot: "bottom-dock",
  id: "flame-graph-panel",
  component: FlameGraphPanel,
  order: 10,
});
panels.register({
  slot: "bottom-status",
  id: "stats-panel",
  component: StatsPanel,
  order: 0,
});
