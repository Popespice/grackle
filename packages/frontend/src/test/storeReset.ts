import type { StoreApi } from "zustand";

/**
 * Full-replace a Zustand store back to the exact state its initializer
 * produced — actions included — before a test runs (campaign T11-4).
 *
 * A partial `setState({...})` in `beforeEach` only resets the fields it names.
 * A test that stubs an ACTION through a partial merge (`setState({ selectNode:
 * vi.fn() })`) therefore leaks that stub into every later test in the module
 * that doesn't re-override it — silently, since the later test still renders.
 * `setState(_, true)` replaces the whole state object instead, so nothing a
 * previous test merged in can survive. This is `CausalPathPanel.test.tsx`'s
 * snapshot-replace pattern, keyed on `getInitialState()` rather than a
 * module-scope snapshot so it cannot be captured after a mutation.
 *
 * Tests that need a specific starting state merge it in AFTER calling this.
 */
export function restoreInitialState<S>(
  store: Pick<StoreApi<S>, "setState" | "getInitialState">
): void {
  store.setState(store.getInitialState(), true);
}
