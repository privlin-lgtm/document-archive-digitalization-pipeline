import { Badge } from "@mantine/core";
import { confidenceColor } from "../utils/confidence";

/** @param value 0-1 */
export function ConfidenceBadge({ value }: { value: number }) {
  return (
    <Badge color={confidenceColor(value)} variant="light" size="sm">
      {Math.round(value * 100)}%
    </Badge>
  );
}
