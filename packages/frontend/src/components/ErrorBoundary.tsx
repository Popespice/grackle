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
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(
      `[ErrorBoundary] panel '${this.props.label}' crashed:`,
      error,
      info.componentStack
    );
  }

  override render(): ReactNode | JSX.Element {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          padding: "var(--space-3)",
          color: "var(--color-error)",
          fontSize: "var(--text-sm)",
          fontFamily: "monospace",
          whiteSpace: "pre-wrap",
        }}
      >
        {this.props.label} crashed: {error.message}
      </div>
    );
  }
}
