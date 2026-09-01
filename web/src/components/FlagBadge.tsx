import { Badge } from "@mantine/core";
import type { FlagSeverity, FlagType } from "../api/types";
import { flagSeverityColor } from "../utils/confidence";

const FLAG_TYPE_LABELS: Record<FlagType, string> = {
  low_ocr_confidence: "Low OCR confidence",
  illegible: "Illegible",
  entity_conflict: "Entity conflict",
  extraction_failure: "Extraction failure",
};

export function FlagBadge({ flagType, severity }: { flagType: FlagType; severity: FlagSeverity }) {
  return (
    <Badge color={flagSeverityColor(severity)} variant="filled" size="sm">
      {FLAG_TYPE_LABELS[flagType] ?? flagType}
    </Badge>
  );
}
