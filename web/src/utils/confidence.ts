// Shared red/amber/green thresholds for confidence-based color-coding.
// Exact cutoffs and colors are a first pass — tuning these against real
// scans/data is exactly the kind of "look at it, nudge it" work this
// scaffold hands off to the interactive pass.

export type ConfidenceLevel = "low" | "medium" | "high";

const LOW_THRESHOLD = 0.6;
const MEDIUM_THRESHOLD = 0.8;

/** @param value 0-1 */
export function confidenceLevel(value: number): ConfidenceLevel {
  if (value < LOW_THRESHOLD) return "low";
  if (value < MEDIUM_THRESHOLD) return "medium";
  return "high";
}

export function confidenceColor(value: number): string {
  switch (confidenceLevel(value)) {
    case "low":
      return "red";
    case "medium":
      return "yellow";
    case "high":
      return "green";
  }
}

const FLAG_SEVERITY_COLORS: Record<string, string> = {
  high: "red",
  medium: "yellow",
  low: "gray",
};

export function flagSeverityColor(severity: string): string {
  return FLAG_SEVERITY_COLORS[severity] ?? "gray";
}
