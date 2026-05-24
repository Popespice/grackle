import type { ComponentType } from "react";

export type Slot =
  | "top-bar"
  | "left-sidebar"
  | "right-sidebar"
  | "floating-overlay"
  | "bottom-dock"
  | "bottom-status";

export interface PanelEntry {
  /** Open string per ADR-0004; unknown slots are no-ops in SlotContainer. */
  slot: string;
  id: string;
  component: ComponentType;
  order: number;
  hideWhen?: () => boolean;
}

export class PanelRegistry {
  private readonly _panels = new Map<string, PanelEntry>();

  register(entry: PanelEntry): void {
    if (this._panels.has(entry.id)) {
      throw new Error(`Panel id '${entry.id}' is already registered`);
    }
    this._panels.set(entry.id, entry);
  }

  getForSlot(slot: string): PanelEntry[] {
    return [...this._panels.values()]
      .filter((p) => p.slot === slot)
      .sort((a, b) => a.order - b.order);
  }
}

export const panels = new PanelRegistry();
