import type { ArgValue } from "@grackle/shared-types";
import type { JSX } from "react";
import { useCallback, useMemo } from "react";
import {
  type AncestorStacks,
  ancestorStackAt,
  nextCallBoundary,
  prevCallBoundary,
  type StackFrame,
} from "../graph/ancestorStack";
import { frameLabel } from "../graph/callTree";
import { useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";

const PANEL_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
  padding: "var(--space-3) var(--space-4)",
  borderTop: "1px solid var(--color-border)",
  background: "var(--color-surface-2)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
  color: "var(--color-text-muted)",
};

const ROW_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-2)",
  flexWrap: "wrap",
};

const BUTTON_STYLE: React.CSSProperties = {
  padding: "2px var(--space-3)",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  color: "var(--color-text)",
  cursor: "pointer",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
};

const SECTION_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-1)",
};

const SECTION_LABEL: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-2)",
  color: "var(--color-text-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  fontSize: "0.9em",
};

/** Shared referentially-stable empty result for the no-stack case. */
const EMPTY_STACKS: AncestorStacks = {
  byThread: new Map<number, StackFrame[]>(),
  activeThreadId: null,
};

const MUTED: React.CSSProperties = { color: "var(--color-text-subtle)" };

const CODE: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  color: "var(--color-accent)",
};

function formatInt(n: number): string {
  return n.toLocaleString();
}

function clampStr(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** A compact `(name=repr, …)` preview of a frame's captured args. */
function argsPreview(args: ArgValue[] | undefined): string {
  if (!args || args.length === 0) return "()";
  return `(${args.map((a) => `${a.name}=${clampStr(a.repr, 28)}`).join(", ")})`;
}

/** Colour a trace-event kind so `call`/`return` read at a glance. */
function kindColor(kind: string): string {
  if (kind === "call") return "var(--color-accent)";
  if (kind === "return") return "var(--color-accent-bright)";
  return "var(--color-text-subtle)";
}

function countOtherActiveThreads(stacks: AncestorStacks): number {
  let n = 0;
  for (const [tid, stack] of stacks.byThread) {
    if (tid !== stacks.activeThreadId && stack.length > 0) n += 1;
  }
  return n;
}

function Badge({
  label,
  title,
}: {
  label: string;
  title: string;
}): JSX.Element {
  return (
    <span
      title={title}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.9em",
        color: "var(--color-warning)",
        border: "1px solid var(--color-warning)",
        borderRadius: "var(--radius-sm)",
        padding: "0 4px",
        opacity: 0.85,
      }}
    >
      {label}
    </span>
  );
}

function ArgRow({ arg }: { arg: ArgValue }): JSX.Element {
  return (
    <li
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: "var(--space-1)",
        flexWrap: "wrap",
      }}
    >
      <span
        style={{ color: "var(--color-accent)", fontFamily: "var(--font-mono)" }}
      >
        {arg.name}
      </span>
      <span style={MUTED}>=</span>
      <span
        style={{
          color: "var(--color-text)",
          fontFamily: "var(--font-mono)",
          wordBreak: "break-all",
        }}
      >
        {arg.repr}
      </span>
      {arg.redacted && (
        <Badge label="redacted" title="Name matched a credential pattern" />
      )}
      {arg.truncated && (
        <Badge label="trunc" title="Value elided to fit size caps" />
      )}
    </li>
  );
}

/**
 * ValueInspectorPanel — time-travel value inspector + call-step navigation
 * (Phase 10.3). Shows the arguments a function was called with and the value it
 * returned as you scrub `tracePlayhead`, plus the live call stack at that point.
 *
 * Frontend-only; consumes 10.2's `values` field. ADR-0007: every hook runs
 * before the `traceSessionId === null` early return; the panel is auto-wrapped
 * in an ErrorBoundary by `SlotContainer`.
 */
export function ValueInspectorPanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const traceWindowStart = useGraphStore((s) => s.traceWindowStart);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceTotal = useGraphStore((s) => s.traceTotal);
  const graph = useGraphStore((s) => s.graph);
  const setPlayhead = useGraphStore((s) => s.setPlayhead);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);

  const full = useFullTrace();

  const graphNodeIds = useMemo(
    () => new Set(graph?.nodes.map((n) => n.id) ?? []),
    [graph]
  );

  // The open stack only changes at call/return boundaries, so snap to the last
  // structural event at/before the playhead and memoize the (O(index)) replay
  // on THAT index — consecutive `line` ticks during playback collapse to a
  // cache hit instead of a full replay every rAF frame.
  const snapIndex = useMemo(() => {
    const events = full.events;
    if (events.length === 0) return -1;
    const ph = Math.min(tracePlayhead, events.length - 1);
    if (ph < 0) return -1;
    const here = events[ph];
    if (here && (here.event === "call" || here.event === "return")) return ph;
    return prevCallBoundary(events, ph) ?? -1;
  }, [full.events, tracePlayhead]);

  const stacks = useMemo(
    (): AncestorStacks =>
      snapIndex >= 0 ? ancestorStackAt(full.events, snapIndex) : EMPTY_STACKS,
    [full.events, snapIndex]
  );

  // Current event at the playhead — prefer the loaded prefix (absolute index),
  // fall back to the store window (`playhead - windowStart`) before load.
  const currentEvent = useMemo(() => {
    if (full.loaded && tracePlayhead < full.events.length) {
      return full.events[tracePlayhead];
    }
    const winIdx = tracePlayhead - traceWindowStart;
    if (winIdx >= 0 && winIdx < traceEvents.length) return traceEvents[winIdx];
    return undefined;
  }, [full.loaded, full.events, tracePlayhead, traceWindowStart, traceEvents]);

  const onSelectFrame = useCallback(
    (frame: StackFrame) => {
      // Only focus a frame that maps to a real static-graph node — selecting an
      // unresolved/imported id would dim the whole Sigma view (FlameGraphPanel
      // precedent). Clear any active highlight so the selection is visible.
      if (!graphNodeIds.has(frame.nodeId)) return;
      setHighlightedNodes(null);
      selectNode(frame.nodeId);
    },
    [graphNodeIds, setHighlightedNodes, selectNode]
  );

  const onPrev = useCallback(() => {
    const idx = prevCallBoundary(full.events, tracePlayhead);
    if (idx !== null) setPlayhead(idx);
  }, [full.events, tracePlayhead, setPlayhead]);

  const onNext = useCallback(() => {
    const idx = nextCallBoundary(full.events, tracePlayhead);
    if (idx !== null) setPlayhead(idx);
  }, [full.events, tracePlayhead, setPlayhead]);

  // Derive the displayable stack once per stack change (not per render) — a
  // `line`-tick render with an unchanged snapIndex reuses it.
  const view = useMemo(() => {
    const activeStack =
      (stacks.activeThreadId !== null
        ? stacks.byThread.get(stacks.activeThreadId)
        : undefined) ?? [];
    return {
      displayFrames: activeStack.slice().reverse(), // innermost first
      otherThreads: countOtherActiveThreads(stacks),
    };
  }, [stacks]);

  // Whether the run captured any values — a bounded prefix scan (values appear
  // on the first sampled calls), so the "enable --capture-values" hint never
  // false-fires on a lone value-less event (e.g. a module-level call) in a
  // capture-ON run. Uses the loaded prefix, else the live store window.
  const captureSeen = useMemo(() => {
    const events = full.loaded ? full.events : traceEvents;
    const limit = Math.min(events.length, 2000);
    for (let i = 0; i < limit; i++) {
      if (events[i]?.values !== undefined) return true;
    }
    return false;
  }, [full.loaded, full.events, traceEvents]);

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (traceSessionId === null) return null;

  const { displayFrames, otherThreads } = view;

  const currentArgs =
    currentEvent && currentEvent.event === "call"
      ? currentEvent.values?.args
      : undefined;
  const isReturn = currentEvent?.event === "return";
  const currentRet =
    currentEvent && currentEvent.event === "return"
      ? currentEvent.values?.ret
      : undefined;
  const currentRetTruncated =
    currentEvent?.event === "return" &&
    currentEvent.values?.ret_truncated === true;

  const total = traceSeekable ? traceTotal : traceEvents.length;
  const canStep = full.events.length > 0;
  const showCaptureHint = currentEvent !== undefined && !captureSeen;

  const renderCurrentEvent = (): JSX.Element => {
    if (!currentEvent)
      return (
        <div style={MUTED}>
          {tracePlayhead >= total
            ? "End of trace."
            : "No event at this position."}
        </div>
      );
    return (
      <>
        <div style={ROW_STYLE}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: kindColor(currentEvent.event),
            }}
          >
            {currentEvent.event}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--color-text)",
            }}
          >
            {frameLabel(currentEvent.node_id)}
          </span>
          <span style={MUTED}>d{currentEvent.frame_depth}</span>
        </div>
        {currentArgs && currentArgs.length > 0 && (
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: "2px",
            }}
          >
            {currentArgs.map((a) => (
              <ArgRow key={a.name} arg={a} />
            ))}
          </ul>
        )}
        {currentEvent.event === "call" &&
          (!currentArgs || currentArgs.length === 0) && (
            <div style={MUTED}>No captured arguments.</div>
          )}
        {isReturn &&
          (currentRet !== undefined ? (
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: "var(--space-1)",
                flexWrap: "wrap",
              }}
            >
              <span style={MUTED}>→</span>
              <span
                style={{
                  color: "var(--color-text)",
                  fontFamily: "var(--font-mono)",
                  wordBreak: "break-all",
                }}
              >
                {currentRet}
              </span>
              {currentRetTruncated && (
                <Badge
                  label="trunc"
                  title="Return value elided to fit size caps"
                />
              )}
            </div>
          ) : (
            <div style={MUTED}>No captured return value.</div>
          ))}
      </>
    );
  };

  const renderStack = (): JSX.Element => {
    if (traceSeekable) {
      if (full.loading) {
        return <div style={MUTED}>Reconstructing call stack…</div>;
      }
      if (full.error) {
        return (
          <div role="alert" style={{ color: "var(--color-error)" }}>
            Failed to load the full trace.{" "}
            <button type="button" onClick={full.load} style={BUTTON_STYLE}>
              Retry
            </button>
          </div>
        );
      }
      if (!full.loaded) {
        return (
          <div style={SECTION_STYLE}>
            <button type="button" onClick={full.load} style={BUTTON_STYLE}>
              Load call stack ({formatInt(traceTotal)} events)
            </button>
            <div style={MUTED}>
              Pages the trace so the stack can be reconstructed. Current-event
              values above update live as you scrub.
            </div>
          </div>
        );
      }
      // A partial (>50k) prefix can only reconstruct the stack for playheads it
      // actually contains — events [0, events.length). At playhead ===
      // events.length (and beyond) the next event was never paged, so the stack
      // is unknown. Gate only when `truncated`: an untruncated prefix is the
      // whole trace, where playhead === events.length is the legitimate
      // "after the final event" position and its stack IS reconstructable.
      if (full.truncated && tracePlayhead >= full.events.length) {
        return (
          <div style={MUTED}>
            Call stack unavailable beyond the first{" "}
            {formatInt(full.events.length)} events. Scrub earlier to inspect the
            stack here.
          </div>
        );
      }
    }

    if (displayFrames.length === 0) {
      return <div style={MUTED}>No open frames at this point.</div>;
    }

    return (
      <ol
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        {displayFrames.map((f, i) => {
          const inGraph = graphNodeIds.has(f.nodeId);
          const redacted = f.args?.some((a) => a.redacted) ?? false;
          return (
            <li
              key={f.callIndex}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-1)",
                background: i === 0 ? "var(--color-surface)" : "transparent",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <button
                type="button"
                onClick={() => onSelectFrame(f)}
                disabled={!inGraph}
                aria-label={`Select ${f.label} frame`}
                title={
                  inGraph
                    ? "Select node + jump to source"
                    : "Frame not in the static graph"
                }
                style={{
                  flex: 1,
                  minWidth: 0,
                  display: "flex",
                  alignItems: "baseline",
                  gap: "var(--space-1)",
                  padding: "2px var(--space-2)",
                  border: "none",
                  background: "transparent",
                  color: "var(--color-text)",
                  cursor: inGraph ? "pointer" : "default",
                  textAlign: "left",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-xs)",
                }}
              >
                <span
                  style={{ color: "var(--color-text-subtle)", flexShrink: 0 }}
                >
                  d{f.depth}
                </span>
                <span style={{ flexShrink: 0 }}>{f.label}</span>
                <span
                  style={{
                    color: "var(--color-text-muted)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {argsPreview(f.args)}
                </span>
                {redacted && (
                  <Badge label="redacted" title="A captured arg was redacted" />
                )}
              </button>
              <button
                type="button"
                onClick={() => setPlayhead(f.callIndex)}
                aria-label={`Jump to ${f.label} call`}
                title="Jump the playhead to this call"
                style={{
                  ...BUTTON_STYLE,
                  padding: "0 var(--space-2)",
                  flexShrink: 0,
                  color: "var(--color-text-muted)",
                }}
              >
                → call
              </button>
            </li>
          );
        })}
      </ol>
    );
  };

  return (
    <section aria-label="Value inspector" style={PANEL_STYLE}>
      <div style={ROW_STYLE}>
        <span style={{ color: "var(--color-text-subtle)", flexShrink: 0 }}>
          Value inspector
        </span>
        <span style={{ fontFamily: "var(--font-mono)" }}>
          event {formatInt(tracePlayhead)} / {formatInt(total)}
        </span>
        {traceSeekable && full.truncated && (
          <span
            title="The stack is reconstructed from the first 50,000 events; scrub within that range."
            style={{ color: "var(--color-warning)" }}
          >
            first 50k only
          </span>
        )}
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={onPrev}
          disabled={!canStep}
          title="Previous call / return"
          style={BUTTON_STYLE}
        >
          ◀ prev
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!canStep}
          title="Next call / return"
          style={BUTTON_STYLE}
        >
          next ▶
        </button>
      </div>

      <div style={SECTION_STYLE}>
        <div style={SECTION_LABEL}>Current event</div>
        {renderCurrentEvent()}
      </div>

      <div style={SECTION_STYLE}>
        <div style={SECTION_LABEL}>
          <span>Call stack</span>
          {stacks.activeThreadId !== null && displayFrames.length > 0 && (
            <span style={{ fontFamily: "var(--font-mono)", ...MUTED }}>
              thread {stacks.activeThreadId}
            </span>
          )}
          {otherThreads > 0 && (
            <span style={MUTED}>
              +{otherThreads} other thread{otherThreads === 1 ? "" : "s"}
            </span>
          )}
        </div>
        {renderStack()}
      </div>

      {showCaptureHint && (
        <div style={{ ...MUTED, fontStyle: "italic" }}>
          No captured values here. Re-run with{" "}
          <code style={CODE}>--capture-values</code> to inspect arguments and
          returns.
        </div>
      )}
    </section>
  );
}
