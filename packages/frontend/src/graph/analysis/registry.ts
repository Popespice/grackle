import type { Graph } from "@grackle/shared-types";

export interface Analysis<T> {
  id: string;
  compute(graph: Graph): T;
  cacheKey(graph: Graph): string;
}

export class AnalysisRegistry {
  private readonly _analyses = new Map<string, Analysis<unknown>>();
  private readonly _cache = new WeakMap<Graph, Map<string, unknown>>();

  register<T>(analysis: Analysis<T>): void {
    if (this._analyses.has(analysis.id)) {
      throw new Error(`Analysis '${analysis.id}' is already registered`);
    }
    this._analyses.set(analysis.id, analysis as Analysis<unknown>);
  }

  get(id: string): Analysis<unknown> | undefined {
    return this._analyses.get(id);
  }

  getAll(): Analysis<unknown>[] {
    return [...this._analyses.values()];
  }

  computeCached<T>(graph: Graph, id: string): T | null {
    const analysis = this._analyses.get(id);
    if (!analysis) return null;

    let graphCache = this._cache.get(graph);
    if (!graphCache) {
      graphCache = new Map();
      this._cache.set(graph, graphCache);
    }

    if (!graphCache.has(id)) {
      graphCache.set(id, analysis.compute(graph));
    }

    return (graphCache.get(id) ?? null) as T | null;
  }
}
