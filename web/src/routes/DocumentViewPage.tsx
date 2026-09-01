import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { Alert, Badge, Center, Group, Loader, Stack, Text } from "@mantine/core";
import { useDocument } from "../hooks/useDocuments";
import { ImageCanvas } from "../components/ImageCanvas";
import { EntityPanel } from "../components/EntityPanel";
import { FlagBadge } from "../components/FlagBadge";

export function DocumentViewPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const { data: document, isLoading, error } = useDocument(documentId);

  const allRegions = useMemo(() => document?.pages.flatMap((page) => page.regions) ?? [], [document]);
  const flaggedRegionIds = useMemo(
    () => new Set((document?.flags ?? []).map((flag) => flag.region_id).filter((id): id is string => id !== null)),
    [document],
  );

  if (isLoading) {
    return (
      <Center h="100%">
        <Loader />
      </Center>
    );
  }

  if (error || !document) {
    return (
      <Alert color="red" title="Failed to load document" m="md">
        {error instanceof Error ? error.message : "Unknown error"}
      </Alert>
    );
  }

  return (
    <Stack h="100%" gap={0}>
      <Group justify="space-between" px="md" py="xs" style={{ borderBottom: "1px solid var(--mantine-color-gray-3)" }}>
        <Stack gap={0}>
          <Text fw={700}>{document.filename}</Text>
          <Text size="xs" c="dimmed">
            Uploaded {new Date(document.upload_time).toLocaleString()}
          </Text>
        </Stack>
        <Group gap="xs">
          <Badge variant="light">{document.status}</Badge>
          {document.flags
            .filter((flag) => flag.status === "open")
            .map((flag) => (
              <FlagBadge key={flag.id} flagType={flag.flag_type} severity={flag.severity} />
            ))}
        </Group>
      </Group>

      <Group grow style={{ flex: 1, minHeight: 0 }} gap={0} align="stretch">
        <div style={{ height: "100%", borderRight: "1px solid var(--mantine-color-gray-3)" }}>
          <ImageCanvas documentId={document.id} regions={allRegions} flaggedRegionIds={flaggedRegionIds} />
        </div>
        <div style={{ height: "100%" }}>
          <EntityPanel pages={document.pages} documentId={document.id} />
        </div>
      </Group>
    </Stack>
  );
}
