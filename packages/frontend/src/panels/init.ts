import { GraphCanvas } from "../graph/GraphCanvas";
import { GraphLegendPanel } from "./GraphLegendPanel";
import { HeaderChrome } from "./HeaderChrome";
import { NodeInspectorPanel } from "./NodeInspectorPanel";
import { panels } from "./registry";
import { SearchFilterPanel } from "./SearchFilterPanel";
import { SourceViewer } from "./SourceViewer";

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
  id: "graph-legend",
  component: GraphLegendPanel,
  order: 20,
});
