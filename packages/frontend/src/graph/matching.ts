import type { GraphNode } from "@grackle/shared-types";

export interface VisibilityOptions {
  searchTerm: string;
  hiddenKinds: ReadonlySet<string>;
  excludeGlobs: string[];
}

/** fnmatch-style: * matches any sequence including path separators. */
function matchGlob(pattern: string, path: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  const regexStr = `^${escaped.replace(/\*/g, ".*").replace(/\?/g, ".")}$`;
  return new RegExp(regexStr).test(path);
}

export function isNodeVisible(
  node: GraphNode,
  opts: VisibilityOptions
): boolean {
  if (opts.hiddenKinds.has(node.kind)) return false;

  for (const glob of opts.excludeGlobs) {
    if (glob.trim() && matchGlob(glob.trim(), node.path)) return false;
  }

  if (opts.searchTerm.length > 0) {
    const term = opts.searchTerm.toLowerCase();
    const qualname =
      typeof node.metadata?.qualname === "string"
        ? node.metadata.qualname.toLowerCase()
        : "";
    if (
      !node.name.toLowerCase().includes(term) &&
      !node.path.toLowerCase().includes(term) &&
      !qualname.includes(term)
    ) {
      return false;
    }
  }

  return true;
}
