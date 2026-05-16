# ADR-0007 — Panel/slot system

**Status:** accepted

## Context

Phase 3 adds six frontend components that need a predictable place in the UI: HeaderChrome, SearchFilterPanel, GraphCanvas, SourceViewer, NodeInspector, and GraphLegend. Hard-coding each into App.tsx as a fixed layout couples the rendering order, hiding behaviour, and grid positioning to a single file. With at least six consumers before Phase 3 closes, ADR-0004's rule-of-three trigger fires twice over; an abstraction is warranted.

The design must work in a browser (single-threaded, ES modules), be testable without touching the singleton, and be consistent with the backend `AdapterRegistry` (ADR-0003) and `KindRegistry` (ADR-0005) patterns.

## Decision

Introduce a **`PanelRegistry`** — a Map-backed singleton with a `register({slot, id, component, order, hideWhen?})` method and a `getForSlot(slot)` accessor. A **`SlotContainer`** component reads from the registry and renders each panel in `order` order.

**Slot names are open strings** per ADR-0004. The five slots in use today (`top-bar`, `left-sidebar`, `floating-overlay`, `right-sidebar`, `bottom-status`) map onto App.tsx's CSS grid regions, but the registry accepts any string. Unknown slots simply produce zero results from `getForSlot`.

**Singleton with injectable override for tests.** `panels` is the module-level singleton. `SlotContainer` accepts an optional `registry` prop so tests instantiate fresh `new PanelRegistry()` objects and never touch the singleton. The singleton stays empty during test runs because `init.ts` is never imported in test files.

**Registration is a side-effect import.** `packages/frontend/src/panels/init.ts` imports every panel component and calls `panels.register(…)`. App.tsx does `import "./panels/init"` at its top — a deliberate side-effect import, the same pattern used by Python's `AdapterRegistry` auto-registration in `grackle/__init__.py`. Module-system caching ensures registration happens exactly once per process.

**`order` is a numeric sort key.** Lower numbers render first within a slot. Panels registered at the same order are rendered in insertion order. Gaps are intentional (order 5, 10, 20) to leave room for insertion without renumbering.

**`hideWhen` is a zero-argument predicate** returning `true` when the panel should not render. `SlotContainer` still mounts the component — visibility is delegated to each panel's own conditional `return null`, which preserves hooks ordering. `hideWhen` is reserved for future use by the slot-level layout (e.g., collapsing an entire sidebar region).

## Consequences

- App.tsx has no direct dependency on individual panel components; new panels self-register.
- Tests use injectable registry instances — the singleton is never corrupted between test runs.
- Duplicate-ID registration throws at module load time, catching conflicts early.
- Slot-ordering conflicts require a manual `order` adjustment; there is no automatic de-duplication.
- Panels must call all React hooks unconditionally (before any early `return null`) to comply with the Rules of Hooks — Biome's `useHookAtTopLevel` rule enforces this.
- Cross-refs: ADR-0003 (registry pattern), ADR-0004 (open strings), ADR-0005 (kind registry pattern).
