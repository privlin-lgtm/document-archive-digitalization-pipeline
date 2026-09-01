import type { FlagSeverity, FlagType } from "../api/types";

// Thresholds and overlay colors tuned for aged-paper scans: CSS "yellow"
// disappears on foxed documents, so medium confidence uses amber. Cutoffs
// sit slightly below a typical "good OCR" line so borderline extractions
// land in amber rather than reading as done.

export type ConfidenceLevel = "low" | "medium" | "high";

const LOW_THRESHOLD = 0.55;
const MEDIUM_THRESHOLD = 0.82;

/** @param value 0-1 */
export function confidenceLevel(value: number): ConfidenceLevel {
  if (value < LOW_THRESHOLD) return "low";
  if (value < MEDIUM_THRESHOLD) return "medium";
  return "high";
}

/** Mantine theme color name — for badges, not for canvas strokes. */
export function confidenceColor(value: number): string {
  switch (confidenceLevel(value)) {
    case "low":
      return "red";
    case "medium":
      return "orange";
    case "high":
      return "teal";
  }
}

export interface OverlayColors {
  stroke: string;
  fill: string;
}

const CONFIDENCE_OVERLAY: Record<ConfidenceLevel, OverlayColors> = {
  low: { stroke: "#e03131", fill: "rgba(224, 49, 49, 0.14)" },
  medium: { stroke: "#f08c00", fill: "rgba(240, 140, 0, 0.12)" },
  high: { stroke: "#2f9e44", fill: "rgba(47, 158, 68, 0.08)" },
};

/** Hex/rgba strokes that stay readable on yellowed paper. */
export function confidenceOverlay(value: number): OverlayColors {
  return CONFIDENCE_OVERLAY[confidenceLevel(value)];
}

export const FLAG_TYPE_OVERLAY: Record<FlagType, OverlayColors> = {
  low_ocr_confidence: { stroke: "#f08c00", fill: "rgba(240, 140, 0, 0.18)" },
  illegible: { stroke: "#c92a2a", fill: "rgba(201, 42, 42, 0.20)" },
  entity_conflict: { stroke: "#9c36b5", fill: "rgba(156, 54, 181, 0.18)" },
  extraction_failure: { stroke: "#1971c2", fill: "rgba(25, 113, 194, 0.16)" },
};

export const FLAG_TYPE_MANTINE: Record<FlagType, string> = {
  low_ocr_confidence: "orange",
  illegible: "red",
  entity_conflict: "grape",
  extraction_failure: "blue",
};

const FLAG_SEVERITY_COLORS: Record<FlagSeverity, string> = {
  high: "red",
  medium: "orange",
  low: "gray",
};

export function flagSeverityColor(severity: string): string {
  return FLAG_SEVERITY_COLORS[severity as FlagSeverity] ?? "gray";
}

export const SEVERITY_RANK: Record<FlagSeverity, number> = {
  high: 3,
  medium: 2,
  low: 1,
};

export const HUMAN_CORRECTED_OVERLAY = {
  accent: "#7048e8",
  fill: "rgba(112, 72, 232, 0.10)",
};

export const SELECTION_RING = "#1c7ed6";
export const HOVER_RING = "rgba(28, 126, 214, 0.55)";
