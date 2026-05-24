import type { ErrorInfo, JSX, ReactNode } from "react";
import { Component, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/index.css";

interface BoundaryState {
  err: Error | null;
  info: ErrorInfo | null;
}

// Demo-branch only: catches and renders uncaught errors so the preview stays
// usable on partial breakage. Production `main.tsx` does not wrap in this.
class DemoErrorBoundary extends Component<
  { children: ReactNode },
  BoundaryState
> {
  state: BoundaryState = { err: null, info: null };
  static getDerivedStateFromError(err: Error): BoundaryState {
    return { err, info: null };
  }
  override componentDidCatch(err: Error, info: ErrorInfo): void {
    console.error("DemoErrorBoundary caught:", err, info);
    this.setState({ err, info });
  }
  override render(): JSX.Element | ReactNode {
    if (this.state.err) {
      return (
        <pre
          style={{
            padding: 24,
            color: "#f87171",
            background: "#0a0a0a",
            fontFamily: "ui-monospace, monospace",
            fontSize: 12,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {this.state.err.stack ?? this.state.err.message}
          {"\n\n— componentStack —\n"}
          {this.state.info?.componentStack ?? "(none)"}
        </pre>
      );
    }
    return this.props.children;
  }
}

const root = document.getElementById("root");
if (root === null) throw new Error("no #root element");

createRoot(root).render(
  <StrictMode>
    <DemoErrorBoundary>
      <App />
    </DemoErrorBoundary>
  </StrictMode>
);
