import { useState } from "react";
import { ActionIcon, Badge, Group, Paper, Stack, Text, TextInput } from "@mantine/core";
import { IconCheck, IconPencil, IconX } from "@tabler/icons-react";
import type { EntityOut, PageOut } from "../api/types";
import { useSelectionStore } from "../state/selection";
import { useCorrectEntity } from "../hooks/useEntityCorrection";
import { ConfidenceBadge } from "./ConfidenceBadge";

const ENTITY_TYPE_LABELS: Record<string, string> = {
  person: "Person",
  date: "Date",
  location: "Location",
  amount: "Amount",
};

function EntityRow({ entity, regionId, documentId }: { entity: EntityOut; regionId: string; documentId: string }) {
  const { selectedEntityId, selectedRegionId, selectEntity } = useSelectionStore();
  const [isEditing, setIsEditing] = useState(false);
  const [draftValue, setDraftValue] = useState(entity.normalized_value ?? entity.raw_text);
  const [reviewer, setReviewer] = useState("");
  const correctEntity = useCorrectEntity(documentId);

  const isSelected = entity.id === selectedEntityId || (regionId === selectedRegionId && !selectedEntityId);

  function startEditing() {
    setDraftValue(entity.normalized_value ?? entity.raw_text);
    setIsEditing(true);
  }

  function submitCorrection() {
    if (!reviewer.trim() || !draftValue.trim()) return;
    correctEntity.mutate(
      { entityId: entity.id, body: { corrected_value: draftValue.trim(), reviewer: reviewer.trim() } },
      { onSuccess: () => setIsEditing(false) },
    );
  }

  return (
    <Paper
      withBorder
      p="xs"
      radius="sm"
      onClick={() => !isEditing && selectEntity(entity.id, regionId)}
      style={{
        cursor: isEditing ? "default" : "pointer",
        borderColor: isSelected ? "#1c7ed6" : undefined,
        backgroundColor: isSelected ? "rgba(28, 126, 214, 0.08)" : undefined,
      }}
    >
      <Group justify="space-between" wrap="nowrap">
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs">
            <Badge variant="outline" size="xs">
              {ENTITY_TYPE_LABELS[entity.entity_type] ?? entity.entity_type}
            </Badge>
            <ConfidenceBadge value={entity.confidence} />
            {entity.corrected && (
              <Badge color="grape" variant="light" size="xs">
                Human-corrected
              </Badge>
            )}
          </Group>

          {isEditing ? (
            <Stack gap={4} mt={4}>
              <TextInput
                size="xs"
                value={draftValue}
                onChange={(event) => setDraftValue(event.currentTarget.value)}
                placeholder="Corrected value"
              />
              <TextInput
                size="xs"
                value={reviewer}
                onChange={(event) => setReviewer(event.currentTarget.value)}
                placeholder="Your name/email (reviewer)"
              />
            </Stack>
          ) : (
            <>
              <Text size="sm" fw={500}>
                {entity.normalized_value ?? entity.raw_text}
              </Text>
              {entity.normalized_value && entity.normalized_value !== entity.raw_text && (
                <Text size="xs" c="dimmed">
                  OCR: “{entity.raw_text}”
                </Text>
              )}
            </>
          )}
        </Stack>

        {isEditing ? (
          <Group gap={4} wrap="nowrap">
            <ActionIcon
              color="green"
              variant="subtle"
              disabled={!reviewer.trim() || !draftValue.trim()}
              loading={correctEntity.isPending}
              onClick={submitCorrection}
              aria-label="Save correction"
            >
              <IconCheck size={16} />
            </ActionIcon>
            <ActionIcon color="gray" variant="subtle" onClick={() => setIsEditing(false)} aria-label="Cancel">
              <IconX size={16} />
            </ActionIcon>
          </Group>
        ) : (
          <ActionIcon
            variant="subtle"
            onClick={(event) => {
              event.stopPropagation();
              startEditing();
            }}
            aria-label="Correct this entity"
          >
            <IconPencil size={16} />
          </ActionIcon>
        )}
      </Group>
    </Paper>
  );
}

export function EntityPanel({ pages, documentId }: { pages: PageOut[]; documentId: string }) {
  if (pages.length === 0) {
    return (
      <Text c="dimmed" p="md">
        No pages processed yet.
      </Text>
    );
  }

  return (
    <Stack gap="md" p="md" style={{ overflowY: "auto", height: "100%" }}>
      {pages.map((page) => (
        <Stack key={page.id} gap="xs">
          <Text size="sm" fw={700}>
            Page {page.page_number}
          </Text>
          {page.regions.map((region) => (
            <Stack key={region.id} gap={4}>
              {region.entities.length === 0 ? null : (
                <>
                  <Text size="xs" c="dimmed" tt="uppercase">
                    {region.region_type}
                  </Text>
                  {region.entities.map((entity) => (
                    <EntityRow key={entity.id} entity={entity} regionId={region.id} documentId={documentId} />
                  ))}
                </>
              )}
            </Stack>
          ))}
        </Stack>
      ))}
    </Stack>
  );
}
