/**
 * Hand-written adapter contracts for the grackle protocol.
 *
 * `language` is an open string everywhere — see ADR-0004 (extension surface).
 * `KNOWN_LANGUAGES` exists for IDE autocomplete and docs, not as an exhaustive enum.
 *
 * Path discipline (ADR-0003): any path-bearing field in these types carries
 * POSIX-normalized relative paths (forward slashes, no leading slash).
 * The generating adapter is responsible for normalization before emission.
 *
 * Review src/generated/adapters.ts after schema changes to confirm intent is preserved.
 */

export const KNOWN_LANGUAGES = ["python", "typescript", "go", "rust"] as const;
export type KnownLanguage = (typeof KNOWN_LANGUAGES)[number];

export interface Capabilities {
  files: boolean;
  classes: boolean;
  functions: boolean;
  imports: boolean;
  calls: boolean;
  runtime_tracing: boolean;
  annotations: boolean;
}

export interface ParseOptions {
  exclude_patterns: readonly string[];
  include_external: boolean;
  follow_imports: boolean;
}

/** Phase-1 skeleton — node/edge shapes defined in phase 2. */
export interface StaticGraph {
  version: number;
  /** Open string; known values in KNOWN_LANGUAGES. See ADR-0004. */
  language: string;
  nodes: readonly unknown[];
  edges: readonly unknown[];
}

/** Phase-1 skeleton — full event schema defined in phase 6. */
export interface TraceEvent {
  id: string;
  /** Unix epoch seconds (float). */
  timestamp: number;
  /** Open string. Common values: "call", "return", "exception". See ADR-0004. */
  type: string;
  payload: Record<string, unknown>;
}
