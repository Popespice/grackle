import { Component, type ErrorInfo, type JSX, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Identifies the failed panel in the fallback UI and console log. */
  label: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Per-panel error isolation. Without this, a throw in any one panel
 * (e.g. GraphCanvas's Sigma init) unmounts React's entire tree, since
 * nothing else in App.tsx catches it — see ADR-0007's SlotContainer.
 *
 * React error boundaries don't reset themselves: without the retry button
 * below, a single throw would latch the panel into its fallback for the
 * rest of the session, even after the underlying data (e.g. a new static
 * graph) becomes valid again — the crashed panel stays unmounted, so it
 * never sees the update.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    // React permits throwing any value, not just Error instances.
    return {
      error: error instanceof Error ? error : new Error(String(error)),
    };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(
      `[ErrorBoundary] panel '${this.props.label}' crashed:`,
      error,
      info.componentStack
    );
  }

  private readonly _retry = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode | JSX.Element {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: "var(--space-2)",
          padding: "var(--space-3)",
          color: "var(--color-error)",
          fontSize: "var(--text-sm)",
          fontFamily: "monospace",
          whiteSpace: "pre-wrap",
        }}
      >
        <span>
          {this.props.label} crashed: {error.message}
        </span>
        <button
          type="button"
          onClick={this._retry}
          style={{
            padding: "var(--space-1) var(--space-2)",
            background: "var(--color-surface-2)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-sm)",
            color: "var(--color-text)",
            fontSize: "var(--text-sm)",
            fontFamily: "inherit",
            cursor: "pointer",
          }}
        >
          Try again
        </button>
      </div>
    );
  }
}
