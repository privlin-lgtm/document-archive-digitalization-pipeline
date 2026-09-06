import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert, Button, Center, Group, Kbd, Loader, Paper, Stack, Text } from "@mantine/core";
import { useHotkeys } from "@mantine/hooks";
import { useReviewFlags, useUpdateReviewFlag } from "../hooks/useReviewFlags";
import { FlagBadge } from "../components/FlagBadge";
import { OffsetPager } from "../components/OffsetPager";

const PAGE_SIZE = 50;

export function ReviewQueuePage() {
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useReviewFlags({ status: "open", limit: PAGE_SIZE, offset });
  const updateFlag = useUpdateReviewFlag();
  const [cursor, setCursor] = useState(0);
  const navigate = useNavigate();
  const itemRefs = useRef<Array<HTMLDivElement | null>>([]);

  const flags = data?.results ?? [];
  const current = flags[cursor];

  useEffect(() => {
    if (cursor >= flags.length && flags.length > 0) {
      setCursor(flags.length - 1);
    }
  }, [flags.length, cursor]);

  useEffect(() => {
    itemRefs.current[cursor]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [cursor]);

  function move(delta: number) {
    setCursor((value) => Math.min(Math.max(value + delta, 0), Math.max(flags.length - 1, 0)));
  }

  function resolve(status: "resolved" | "dismissed") {
    if (!current || updateFlag.isPending) return;
    updateFlag.mutate({ flagId: current.id, body: { status } });
  }

  // Bare single-letter shortcuts collide with screen readers' own browse-mode
  // quick-nav keys (WCAG 2.1.4) -- requiring Alt keeps the shortcuts without
  // stealing keys assistive tech already uses. Arrow keys/Enter are left
  // bare: they aren't printable-character shortcuts, so 2.1.4 doesn't apply.
  useHotkeys([
    ["alt+j", () => move(1)],
    ["ArrowDown", () => move(1)],
    ["alt+k", () => move(-1)],
    ["ArrowUp", () => move(-1)],
    ["alt+s", () => move(1)],
    ["alt+a", () => resolve("resolved")],
    ["alt+r", () => resolve("resolved")],
    ["alt+x", () => resolve("dismissed")],
    [
      "Enter",
      () => {
        if (current) navigate(`/documents/${current.document_id}?flag=${current.id}`);
      },
    ],
  ]);

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
          <Kbd>Alt</Kbd>+<Kbd>J</Kbd>/<Kbd>Alt</Kbd>+<Kbd>K</Kbd>{" "}
          <Text size="xs" c="dimmed" span>
            next/prev
          </Text>
          <Kbd>Alt</Kbd>+<Kbd>A</Kbd>{" "}
          <Text size="xs" c="dimmed" span>
            approve
          </Text>
          <Kbd>Alt</Kbd>+<Kbd>X</Kbd>{" "}
          <Text size="xs" c="dimmed" span>
            dismiss
          </Text>
          <Kbd>Alt</Kbd>+<Kbd>S</Kbd>{" "}
          <Text size="xs" c="dimmed" span>
            skip
          </Text>
          <Kbd>↵</Kbd>{" "}
          <Text size="xs" c="dimmed" span>
            open
          </Text>
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
              className="review-queue-item"
              data-active={index === cursor ? "true" : "false"}
              ref={(node) => {
                itemRefs.current[index] = node;
              }}
              onClick={() => setCursor(index)}
              onDoubleClick={() => navigate(`/documents/${flag.document_id}?flag=${flag.id}`)}
              style={{ cursor: "pointer" }}
            >
              <Group justify="space-between" wrap="nowrap">
                <Stack gap={2} style={{ minWidth: 0, flex: 1 }}>
                  <Group gap="xs">
                    <FlagBadge flagType={flag.flag_type} severity={flag.severity} />
                    <Link to={`/documents/${flag.document_id}?flag=${flag.id}`} onClick={(event) => event.stopPropagation()}>
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
                    onClick={(event) => {
                      event.stopPropagation();
                      updateFlag.mutate({ flagId: flag.id, body: { status: "resolved" } });
                    }}
                  >
                    Resolve
                  </Button>
                  <Button
                    size="xs"
                    variant="light"
                    color="gray"
                    onClick={(event) => {
                      event.stopPropagation();
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
      {data ? <OffsetPager total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} /> : null}
    </Stack>
  );
}
