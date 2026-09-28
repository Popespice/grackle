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
 * The initial state object itself is not safe to hand back, though: a store
 * that keeps a `Set` or `Map` in state and mutates it in place (`useGrackleClient`'s
 * handler sets and pending-request maps) mutates that very object, so putting it
 * back would reinstate whatever a previous test left in the collection. Each
 * top-level `Set`/`Map` is therefore rebuilt from the contents it had when this
 * store was first reset — the first `beforeEach`, before any test has run in
 * this module.
 *
 * Tests that need a specific starting state merge it in AFTER calling this.
 */
type Collection = Set<unknown> | Map<unknown, unknown>;

const firstSeen = new WeakMap<object, Map<string, Collection>>();

function collectionsOf(state: object): Map<string, Collection> {
  const found = new Map<string, Collection>();
  for (const [key, value] of Object.entries(state)) {
    if (value instanceof Set) found.set(key, new Set(value));
    else if (value instanceof Map) found.set(key, new Map(value));
  }
  return found;
}

export function restoreInitialState<S>(
  store: Pick<StoreApi<S>, "setState" | "getInitialState">
): void {
  const initial = store.getInitialState();
  let pristine = firstSeen.get(store);
  if (pristine === undefined) {
    pristine = collectionsOf(initial as object);
    firstSeen.set(store, pristine);
  }
  const fresh: Record<string, Collection> = {};
  for (const [key, value] of pristine) {
    fresh[key] = value instanceof Set ? new Set(value) : new Map(value);
  }
  store.setState(Object.assign({}, initial, fresh) as S, true);
}
