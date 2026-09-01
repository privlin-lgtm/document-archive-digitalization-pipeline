import { create } from "zustand";

/**
 * Shared "what's currently selected" state for the document view — the
 * image canvas and entity panel both read/write this so clicking a region
 * on the image highlights its entities and vice versa, without threading
 * selection state as props through every layer in between.
 */
interface SelectionState {
  selectedRegionId: string | null;
  selectedEntityId: string | null;
  selectRegion: (regionId: string | null) => void;
  selectEntity: (entityId: string | null, regionId: string | null) => void;
  clear: () => void;
}

export const useSelectionStore = create<SelectionState>((set) => ({
  selectedRegionId: null,
  selectedEntityId: null,
  selectRegion: (regionId) => set({ selectedRegionId: regionId, selectedEntityId: null }),
  selectEntity: (entityId, regionId) => set({ selectedEntityId: entityId, selectedRegionId: regionId }),
  clear: () => set({ selectedRegionId: null, selectedEntityId: null }),
}));
