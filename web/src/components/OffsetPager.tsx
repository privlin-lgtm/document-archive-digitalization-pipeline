import { Button, Group, Text } from "@mantine/core";

export function OffsetPager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);
  return (
    <Group justify="space-between" mt="sm">
      <Text size="sm" c="dimmed">
        {start}–{end} of {total}
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="default" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button
          size="xs"
          variant="default"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </Button>
      </Group>
    </Group>
  );
}
