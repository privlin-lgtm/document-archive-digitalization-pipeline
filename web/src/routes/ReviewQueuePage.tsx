import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Center, Group, Kbd, Loader, Paper, Stack, Text } from "@mantine/core";
import { useReviewFlags, useUpdateReviewFlag } from "../hooks/useReviewFlags";
import { FlagBadge } from "../components/FlagBadge";

/**
 * Keyboard shortcuts (first pass — exact keys/feel are a good candidate
 * for the interactive tuning pass): j/↓ next, k/↑ prev, r resolve
 * (+ advance), x dismiss (+ advance), s skip (advance without acting).
 */
export function ReviewQueuePage() {
  const { data, isLoading, error } = useReviewFlags({ status: "open" });
  const updateFlag = useUpdateReviewFlag();
  const [cursor, setCursor] = useState(0);

  const flags = data?.results ?? [];
  const current = flags[cursor];

  useEffect(() => {
    if (cursor >= flags.length && flags.length > 0) {
      setCursor(flags.length - 1);
    }
  }, [flags.length, cursor]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;

      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setCursor((c) => Math.min(c + 1, flags.length - 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
      } else if (event.key === "s") {
        setCursor((c) => Math.min(c + 1, flags.length - 1));
      } else if ((event.key === "r" || event.key === "x") && current) {
        updateFlag.mutate({ flagId: current.id, body: { status: event.key === "r" ? "resolved" : "dismissed" } });
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [flags.length, current, updateFlag]);

  if (isLoading) {
    return (
      <Center h="100%">
        <Loader />
      </Center>
    );
  }
  if (error) {
    return (
      <Alert color="red" title="Failed to load review queue" m="md">
        {error instanceof Error ? error.message : "Unknown error"}
      </Alert>
    );
  }

  return (
    <Stack p="md" gap="md">
      <Group justify="space-between">
        <Text fw={700} size="lg">
          Review queue ({data?.total ?? 0} open)
        </Text>
        <Group gap={6}>
          <Kbd>j</Kbd>/<Kbd>k</Kbd> <Text size="xs" c="dimmed" span>next/prev</Text>
          <Kbd>r</Kbd> <Text size="xs" c="dimmed" span>resolve</Text>
          <Kbd>x</Kbd> <Text size="xs" c="dimmed" span>dismiss</Text>
          <Kbd>s</Kbd> <Text size="xs" c="dimmed" span>skip</Text>
        </Group>
      </Group>

      {flags.length === 0 ? (
        <Text c="dimmed">No open flags. Nothing to review.</Text>
      ) : (
        <Stack gap="xs">
          {flags.map((flag, index) => (
            <Paper
              key={flag.id}
              withBorder
              p="sm"
              radius="sm"
              onClick={() => setCursor(index)}
              style={{
                cursor: "pointer",
                borderColor: index === cursor ? "#1c7ed6" : undefined,
                backgroundColor: index === cursor ? "rgba(28, 126, 214, 0.06)" : undefined,
              }}
            >
              <Group justify="space-between" wrap="nowrap">
                <Stack gap={2} style={{ minWidth: 0, flex: 1 }}>
                  <Group gap="xs">
                    <FlagBadge flagType={flag.flag_type} severity={flag.severity} />
                    <Link to={`/documents/${flag.document_id}`} onClick={(e) => e.stopPropagation()}>
                      <Text size="sm" fw={500} truncate>
                        {flag.document_filename}
                      </Text>
                    </Link>
                  </Group>
                  <Text size="sm" c="dimmed">
                    {flag.explanation}
                  </Text>
                </Stack>
                <Group gap="xs" wrap="nowrap">
                  <Button
                    size="xs"
                    variant="light"
                    color="green"
                    loading={updateFlag.isPending && current?.id === flag.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      updateFlag.mutate({ flagId: flag.id, body: { status: "resolved" } });
                    }}
                  >
                    Resolve
                  </Button>
                  <Button
                    size="xs"
                    variant="light"
                    color="gray"
                    onClick={(e) => {
                      e.stopPropagation();
                      updateFlag.mutate({ flagId: flag.id, body: { status: "dismissed" } });
                    }}
                  >
                    Dismiss
                  </Button>
                </Group>
              </Group>
            </Paper>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
