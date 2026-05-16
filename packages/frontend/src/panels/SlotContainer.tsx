import type { JSX } from "react";
import { panels as defaultPanels, type PanelRegistry } from "./registry";

interface SlotContainerProps {
  slot: string;
  /** Override the registry; used in tests to avoid touching the singleton. */
  registry?: PanelRegistry;
}

export function SlotContainer({
  slot,
  registry = defaultPanels,
}: SlotContainerProps): JSX.Element | null {
  const entries = registry.getForSlot(slot);
  if (entries.length === 0) return null;
  return (
    <>
      {entries.map(({ id, component: Panel }) => (
        <Panel key={id} />
      ))}
    </>
  );
}
