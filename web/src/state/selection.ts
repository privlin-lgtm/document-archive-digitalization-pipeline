import { create } from "zustand";

/**
 * Shared "what's currently selected / hovered" state for the document view.
 * ImageCanvas and EntityPanel both read/write this so clicking a region on
 * the image highlights its entities and vice versa, without threading
 * selection as props through every layer in between.
 *
 * `focusGeneration` bumps when selection originates from the panel or
 * keyboard so the canvas can pan the region into view — canvas-originated
 * clicks skip that, because the region is already under the pointer.
 */
export type SelectionSource = "canvas" | "panel" | "keyboard";

interface SelectionState {
  selectedRegionId: string | null;
  selectedEntityId: string | null;
  selectedFlagId: string | null;
  hoveredRegionId: string | null;
  hoveredEntityId: string | null;
  editingEntityId: string | null;
  focusGeneration: number;
  selectRegion: (regionId: string | null, source?: SelectionSource) => void;
  selectEntity: (entityId: string | null, regionId: string | null, source?: SelectionSource) => void;
  selectFlag: (
    flagId: string | null,
    regionId: string | null,
    entityId: string | null,
  ) => void;
  hoverRegion: (regionId: string | null) => void;
  hoverEntity: (entityId: string | null, regionId: string | null) => void;
  setEditingEntityId: (entityId: string | null) => void;
  clear: () => void;
}

export const useSelectionStore = create<SelectionState>((set) => ({
  selectedRegionId: null,
  selectedEntityId: null,
  selectedFlagId: null,
  hoveredRegionId: null,
  hoveredEntityId: null,
  editingEntityId: null,
  focusGeneration: 0,
  selectRegion: (regionId, source = "panel") =>
    set((state) => ({
      selectedRegionId: regionId,
      selectedEntityId: null,
      selectedFlagId: null,
      editingEntityId: null,
      focusGeneration: source === "canvas" ? state.focusGeneration : state.focusGeneration + 1,
    })),
  selectEntity: (entityId, regionId, source = "panel") =>
    set((state) => ({
      selectedEntityId: entityId,
      selectedRegionId: regionId,
      selectedFlagId: null,
      focusGeneration: source === "canvas" ? state.focusGeneration : state.focusGeneration + 1,
    })),
  selectFlag: (flagId, regionId, entityId) =>
    set((state) => ({
      selectedFlagId: flagId,
      selectedRegionId: regionId,
      selectedEntityId: entityId,
      editingEntityId: null,
      focusGeneration: state.focusGeneration + 1,
    })),
  hoverRegion: (regionId) => set({ hoveredRegionId: regionId, hoveredEntityId: null }),
  hoverEntity: (entityId, regionId) => set({ hoveredEntityId: entityId, hoveredRegionId: regionId }),
  setEditingEntityId: (entityId) => set({ editingEntityId: entityId }),
  clear: () =>
    set({
      selectedRegionId: null,
      selectedEntityId: null,
      selectedFlagId: null,
      hoveredRegionId: null,
      hoveredEntityId: null,
      editingEntityId: null,
    }),
}));
