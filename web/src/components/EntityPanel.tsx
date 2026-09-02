import { useEffect, useRef, useState } from "react";
import { ActionIcon, Badge, Group, Paper, Stack, Text, TextInput } from "@mantine/core";
import { useHotkeys } from "@mantine/hooks";
import { IconCheck, IconPencil, IconX } from "@tabler/icons-react";
import type { EntityOut, PageOut, RegionOut } from "../api/types";
import { useSelectionStore } from "../state/selection";
import { useCorrectEntity } from "../hooks/useEntityCorrection";
import { ConfidenceBadge } from "./ConfidenceBadge";

const ENTITY_TYPE_LABELS: Record<string, string> = {
  person: "Person",
  date: "Date",
  location: "Location",
  amount: "Amount",
};

const REGION_TYPE_LABELS: Record<string, string> = {
  paragraph: "Paragraph",
  table: "Table",
  signature: "Signature",
  margin_annotation: "Margin note",
  stamp: "Stamp",
};

function EntityRow({
  entity,
  regionId,
  documentId,
}: {
  entity: EntityOut;
  regionId: string;
  documentId: string;
}) {
  const selectedEntityId = useSelectionStore((s) => s.selectedEntityId);
  const selectedRegionId = useSelectionStore((s) => s.selectedRegionId);
  const hoveredEntityId = useSelectionStore((s) => s.hoveredEntityId);
  const hoveredRegionId = useSelectionStore((s) => s.hoveredRegionId);
  const editingEntityId = useSelectionStore((s) => s.editingEntityId);
  const selectEntity = useSelectionStore((s) => s.selectEntity);
  const hoverEntity = useSelectionStore((s) => s.hoverEntity);
  const setEditingEntityId = useSelectionStore((s) => s.setEditingEntityId);
  const [draftValue, setDraftValue] = useState(entity.normalized_value ?? entity.raw_text);
  const correctEntity = useCorrectEntity(documentId);
  const inputRef = useRef<HTMLInputElement>(null);

  const isSelected = entity.id === selectedEntityId || (regionId === selectedRegionId && !selectedEntityId);
  const isHovered = entity.id === hoveredEntityId || (regionId === hoveredRegionId && !hoveredEntityId);
  const isEditing = entity.id === editingEntityId;

  useEffect(() => {
    if (isEditing) {
      setDraftValue(entity.normalized_value ?? entity.raw_text);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [entity.normalized_value, entity.raw_text, isEditing]);

  function submitCorrection() {
    if (!draftValue.trim()) return;
    correctEntity.mutate(
      { entityId: entity.id, body: { corrected_value: draftValue.trim() } },
      { onSuccess: () => setEditingEntityId(null) },
    );
  }

  return (
    <Paper
      withBorder
      p="xs"
      radius="sm"
      data-focus-id={entity.id}
      onClick={() => {
        if (!isEditing) selectEntity(entity.id, regionId, "panel");
      }}
      onMouseEnter={() => hoverEntity(entity.id, regionId)}
      onMouseLeave={() => hoverEntity(null, null)}
      role={isEditing ? undefined : "button"}
      tabIndex={isEditing ? -1 : 0}
      aria-pressed={isSelected}
      className="entity-row"
      data-selected={isSelected ? "true" : "false"}
      data-hovered={isHovered && !isSelected ? "true" : "false"}
      data-corrected={entity.corrected ? "true" : "false"}
      style={{ cursor: isEditing ? "default" : "pointer" }}
    >
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs">
            <Badge variant="outline" size="xs">
              {ENTITY_TYPE_LABELS[entity.entity_type] ?? entity.entity_type}
            </Badge>
            <ConfidenceBadge value={entity.confidence} />
            {entity.corrected ? (
              <Badge color="grape" variant="light" size="xs">
                Human-corrected
              </Badge>
            ) : (
              <Badge color="gray" variant="outline" size="xs">
                AI-extracted
              </Badge>
            )}
          </Group>

          {isEditing ? (
            <TextInput
              ref={inputRef}
              size="xs"
              value={draftValue}
              onChange={(event) => setDraftValue(event.currentTarget.value)}
              placeholder="Corrected value"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  submitCorrection();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  setEditingEntityId(null);
                }
              }}
              onClick={(event) => event.stopPropagation()}
            />
          ) : (
            <>
              <Text size="sm" fw={600} lineClamp={2}>
                {entity.normalized_value ?? entity.raw_text}
              </Text>
              {entity.normalized_value && entity.normalized_value !== entity.raw_text ? (
                <Text size="xs" c="dimmed" lineClamp={1}>
                  OCR: “{entity.raw_text}”
                </Text>
              ) : null}
            </>
          )}
        </Stack>

        {isEditing ? (
          <Group gap={4} wrap="nowrap">
            <ActionIcon
              color="green"
              variant="subtle"
              disabled={!draftValue.trim()}
              loading={correctEntity.isPending}
              onClick={(event) => {
                event.stopPropagation();
                submitCorrection();
              }}
              aria-label="Save correction"
            >
              <IconCheck size={16} />
            </ActionIcon>
            <ActionIcon
              color="gray"
              variant="subtle"
              onClick={(event) => {
                event.stopPropagation();
                setEditingEntityId(null);
              }}
              aria-label="Cancel"
            >
              <IconX size={16} />
            </ActionIcon>
          </Group>
        ) : (
          <ActionIcon
            variant="subtle"
            onClick={(event) => {
              event.stopPropagation();
              selectEntity(entity.id, regionId, "panel");
              setEditingEntityId(entity.id);
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

function RegionBlock({
  region,
  documentId,
}: {
  region: RegionOut;
  documentId: string;
}) {
  const selectedRegionId = useSelectionStore((s) => s.selectedRegionId);
  const selectedEntityId = useSelectionStore((s) => s.selectedEntityId);
  const hoveredRegionId = useSelectionStore((s) => s.hoveredRegionId);
  const selectRegion = useSelectionStore((s) => s.selectRegion);
  const hoverRegion = useSelectionStore((s) => s.hoverRegion);
  const isSelected = region.id === selectedRegionId && !selectedEntityId;
  const isHovered = region.id === hoveredRegionId;

  return (
    <Stack
      gap={4}
      data-focus-id={region.id}
      className="region-block"
      data-selected={isSelected ? "true" : "false"}
      data-hovered={isHovered && !isSelected ? "true" : "false"}
    >
      <Text
        size="xs"
        c="dimmed"
        tt="uppercase"
        fw={600}
        className="region-block-label"
        onClick={() => selectRegion(region.id, "panel")}
        onMouseEnter={() => hoverRegion(region.id)}
        onMouseLeave={() => hoverRegion(null)}
      >
        {REGION_TYPE_LABELS[region.region_type] ?? region.region_type}
        {region.ocr_result ? ` · ${Math.round(region.ocr_result.confidence)}% OCR` : null}
      </Text>
      {region.entities.length === 0 ? (
        <Paper withBorder p="xs" radius="sm" className="entity-row" data-selected={isSelected ? "true" : "false"}>
          <Text size="xs" c="dimmed">
            {region.ocr_result?.text?.trim() || "No entities in this region"}
          </Text>
        </Paper>
      ) : (
        region.entities.map((entity) => (
          <EntityRow
            key={entity.id}
            entity={entity}
            regionId={region.id}
            documentId={documentId}
          />
        ))
      )}
    </Stack>
  );
}

export function EntityPanel({ pages, documentId }: { pages: PageOut[]; documentId: string }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const selectedEntityId = useSelectionStore((s) => s.selectedEntityId);
  const selectedRegionId = useSelectionStore((s) => s.selectedRegionId);
  const setEditingEntityId = useSelectionStore((s) => s.setEditingEntityId);

  useEffect(() => {
    const id = selectedEntityId ?? selectedRegionId;
    if (!id || !panelRef.current) return;
    const el = panelRef.current.querySelector(`[data-focus-id="${id}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedEntityId, selectedRegionId]);

  useHotkeys([
    [
      "e",
      () => {
        if (selectedEntityId) setEditingEntityId(selectedEntityId);
      },
    ],
  ]);

  if (pages.length === 0) {
    return (
      <Text c="dimmed" p="md">
        No pages processed yet.
      </Text>
    );
  }

  return (
    <Stack gap="sm" p="md" className="entity-panel" ref={panelRef}>
      {pages.map((page) => (
        <Stack key={page.id} gap="xs">
          <Text size="sm" fw={700}>
            Page {page.page_number}
          </Text>
          {page.regions.map((region) => (
            <RegionBlock
              key={region.id}
              region={region}
              documentId={documentId}
            />
          ))}
        </Stack>
      ))}
    </Stack>
  );
}
