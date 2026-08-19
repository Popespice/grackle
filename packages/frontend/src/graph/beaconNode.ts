/**
 * Node-id matching for `packages/nn`'s trace beacons (Phase 12.4).
 *
 * A trace `node_id` is `<posix-relative-path>:<qualname>` (single colon — the
 * qualname never contains one). Every beacon consumer needs the same guard: a
 * bare `endsWith("metrics.py:record_epoch")` would also match a node_id like
 * `pkg/mymetrics.py:record_epoch`, which is a DIFFERENT module. The suffix
 * must therefore be either the whole node_id or preceded by a path separator.
 *
 * Extracted rather than mirrored: this guard was hand-inlined in four modules
 * (`epochSeries.ts`, `networkSpec.ts`, `layerStats.ts`, `layerActivity.ts`),
 * so a refinement had to be found and applied four times and a missed one
 * fails silently (a mismatched beacon renders nothing, it does not throw).
 */

/** Exact-or-slash-preceded match of a full `<file>:<qualname>` suffix. */
export function matchesBeaconNode(nodeId: string, suffix: string): boolean {
  return nodeId === suffix || nodeId.endsWith(`/${suffix}`);
}

/**
 * Match `<file>:<Class>.<method>` ignoring the class name — the several
 * `Linear`/`ReLU`/`Tanh` classes in `layers.py` all expose `forward`, and the
 * network view cares which FILE and which METHOD fired, not which class.
 *
 * Splits on the LAST colon: the `path:qualname` scheme guarantees the qualname
 * is colon-free, so this separates the two unambiguously no matter what the
 * (POSIX-relative, so colon-free in practice) path contains.
 */
export function matchesBeaconMethod(
  nodeId: string,
  file: string,
  method: string
): boolean {
  const colon = nodeId.lastIndexOf(":");
  if (colon === -1) return false;
  const path = nodeId.slice(0, colon);
  if (path !== file && !path.endsWith(`/${file}`)) return false;
  const qualname = nodeId.slice(colon + 1);
  const dot = qualname.lastIndexOf(".");
  if (dot === -1) return false;
  return qualname.slice(dot + 1) === method;
}
