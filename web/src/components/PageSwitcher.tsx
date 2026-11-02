import { ActionIcon, Group, Text, Tooltip } from "@mantine/core";
import { IconChevronLeft, IconChevronRight } from "@tabler/icons-react";

interface PageSwitcherProps {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  /** Open-flag count per page number, shown so reviewers know where work is. */
  openFlagsByPage?: Map<number, number>;
}

/**
 * Previous/next control for multi-page documents (PDFs, multi-frame TIFFs).
 * Renders nothing for single-page documents.
 */
export function PageSwitcher({ page, pageCount, onChange, openFlagsByPage }: PageSwitcherProps) {
  if (pageCount <= 1) return null;
  const flagsHere = openFlagsByPage?.get(page) ?? 0;
  return (
    <Group gap={4} wrap="nowrap" role="group" aria-label="Page navigation">
      <Tooltip label="Previous page (PageUp)">
        <ActionIcon
          size="sm"
          variant="light"
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <IconChevronLeft size={14} />
        </ActionIcon>
      </Tooltip>
      <Text size="xs" aria-live="polite" style={{ whiteSpace: "nowrap" }}>
        Page {page} of {pageCount}
        {flagsHere > 0 ? ` · ${flagsHere} open flag${flagsHere === 1 ? "" : "s"}` : ""}
      </Text>
      <Tooltip label="Next page (PageDown)">
        <ActionIcon
          size="sm"
          variant="light"
          onClick={() => onChange(page + 1)}
          disabled={page >= pageCount}
          aria-label="Next page"
        >
          <IconChevronRight size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}
