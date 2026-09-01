import { useEffect, useMemo, useRef } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Alert, Badge, Group, Kbd, Loader, Stack, Text } from "@mantine/core";
import { useHotkeys } from "@mantine/hooks";
import { useDocument } from "../hooks/useDocuments";
import { useUpdateReviewFlag } from "../hooks/useReviewFlags";
import { ImageCanvas, type ImageCanvasHandle } from "../components/ImageCanvas";
import { EntityPanel } from "../components/EntityPanel";
import { FlagBadge } from "../components/FlagBadge";
import { SplitPane } from "../components/SplitPane";
import { useSelectionStore } from "../state/selection";
import { SEVERITY_RANK } from "../utils/confidence";
import type { FlagType, ReviewFlagOut } from "../api/types";

function OverlayLegend() {
  return (
    <Group gap="md" wrap="wrap">
      <Group gap={6}>
        <span className="legend-swatch" style={{ background: "#e03131" }} />
        <Text size="xs" c="dimmed">
          Low conf.
        </Text>
      </Group>
      <Group gap={6}>
        <span className="legend-swatch" style={{ background: "#f08c00" }} />
        <Text size="xs" c="dimmed">
          Medium
        </Text>
      </Group>
      <Group gap={6}>
        <span className="legend-swatch" style={{ background: "#2f9e44" }} />
        <Text size="xs" c="dimmed">
          High
        </Text>
      </Group>
      <Group gap={6}>
        <span className="legend-swatch legend-swatch-dashed" />
        <Text size="xs" c="dimmed">
          Flagged (dashed, by type)
        </Text>
      </Group>
      <Group gap={6}>
        <span className="legend-swatch" style={{ background: "#7048e8" }} />
        <Text size="xs" c="dimmed">
          Human-corrected
        </Text>
      </Group>
    </Group>
  );
}

export function DocumentViewPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const [searchParams] = useSearchParams();
  const { data: document, isLoading, error } = useDocument(documentId);
  const updateFlag = useUpdateReviewFlag();
  const canvasRef = useRef<ImageCanvasHandle>(null);
  const initializedDocumentId = useRef<string | null>(null);

  const selectedFlagId = useSelectionStore((s) => s.selectedFlagId);
  const editingEntityId = useSelectionStore((s) => s.editingEntityId);
  const selectFlag = useSelectionStore((s) => s.selectFlag);
  const setEditingEntityId = useSelectionStore((s) => s.setEditingEntityId);
  const clear = useSelectionStore((s) => s.clear);

  const allRegions = useMemo(() => document?.pages.flatMap((page) => page.regions) ?? [], [document]);
  const openFlags = useMemo(
    () =>
      (document?.flags ?? [])
        .filter((flag) => flag.status === "open")
        .sort(
          (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.created_at.localeCompare(b.created_at),
        ),
    [document],
  );
  const flagsByRegionId = useMemo(() => {
    const map = new Map<string, { type: FlagType; rank: number }>();
    for (const flag of openFlags) {
      if (!flag.region_id) continue;
      const rank = SEVERITY_RANK[flag.severity];
      const existing = map.get(flag.region_id);
      if (!existing || rank > existing.rank) {
        map.set(flag.region_id, { type: flag.flag_type, rank });
      }
    }
    return new Map([...map.entries()].map(([id, value]) => [id, value.type]));
  }, [openFlags]);
  const correctedRegionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const region of allRegions) {
      if (region.entities.some((entity) => entity.corrected)) ids.add(region.id);
    }
    return ids;
  }, [allRegions]);

  const currentFlag: ReviewFlagOut | undefined =
    openFlags.find((flag) => flag.id === selectedFlagId) ?? openFlags[0];
  const currentFlagIndex = currentFlag ? openFlags.findIndex((flag) => flag.id === currentFlag.id) : -1;

  useEffect(() => {
    initializedDocumentId.current = null;
    return () => clear();
  }, [clear, documentId]);

  useEffect(() => {
    if (!document || initializedDocumentId.current === document.id) return;
    initializedDocumentId.current = document.id;
    const requested = searchParams.get("flag");
    const flag =
      document.flags.find((item) => item.id === requested) ??
      openFlags[0];
    if (flag) selectFlag(flag.id, flag.region_id, flag.entity_id);
  }, [document, openFlags, searchParams, selectFlag]);

  function moveFlag(delta: number) {
    if (openFlags.length === 0) return;
    const from = currentFlagIndex >= 0 ? currentFlagIndex : 0;
    const next = Math.min(openFlags.length - 1, Math.max(0, from + delta));
    const flag = openFlags[next];
    selectFlag(flag.id, flag.region_id, flag.entity_id);
  }

  function resolveCurrent(status: "resolved" | "dismissed") {
    if (!currentFlag) return;
    const next = openFlags[currentFlagIndex + 1] ?? openFlags[currentFlagIndex - 1] ?? null;
    updateFlag.mutate(
      { flagId: currentFlag.id, body: { status } },
      {
        onSuccess: () => {
          if (next) selectFlag(next.id, next.region_id, next.entity_id);
          else selectFlag(null, null, null);
        },
      },
    );
  }

  useHotkeys([
    ["j", () => moveFlag(1), { preventDefault: true }],
    ["ArrowDown", () => moveFlag(1), { preventDefault: true }],
    ["k", () => moveFlag(-1), { preventDefault: true }],
    ["ArrowUp", () => moveFlag(-1), { preventDefault: true }],
    ["a", () => resolveCurrent("resolved")],
    ["r", () => resolveCurrent("resolved")],
    ["x", () => resolveCurrent("dismissed")],
    ["s", () => moveFlag(1), { preventDefault: true }],
    [
      "Escape",
      () => {
        if (editingEntityId) {
          setEditingEntityId(null);
          return;
        }
        clear();
      },
    ],
    ["=", () => canvasRef.current?.zoomBy(1.15)],
    ["+", () => canvasRef.current?.zoomBy(1.15)],
    ["-", () => canvasRef.current?.zoomBy(1 / 1.15)],
    ["0", () => canvasRef.current?.fit()],
  ]);

  if (isLoading) {
    return (
      <Group h="100%" justify="center">
        <Loader />
      </Group>
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
    <Stack h="100%" gap={0} className="document-view">
      <Group
        justify="space-between"
        px="md"
        py="xs"
        wrap="nowrap"
        className="document-view-header"
      >
        <Stack gap={0} style={{ minWidth: 0 }}>
          <Text fw={700} truncate>
            {document.filename}
          </Text>
          <Text size="xs" c="dimmed">
            Uploaded {new Date(document.upload_time).toLocaleString()}
          </Text>
        </Stack>
        <Group gap="xs" wrap="nowrap">
          <Badge variant="light">{document.status}</Badge>
          {openFlags.slice(0, 4).map((flag) => (
            <FlagBadge key={flag.id} flagType={flag.flag_type} severity={flag.severity} />
          ))}
          {openFlags.length > 4 ? (
            <Badge variant="outline" color="gray">
              +{openFlags.length - 4}
            </Badge>
          ) : null}
        </Group>
      </Group>

      {currentFlag ? (
        <Group px="md" py={6} justify="space-between" wrap="nowrap" className="document-view-flagbar">
          <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
            <FlagBadge flagType={currentFlag.flag_type} severity={currentFlag.severity} />
            <Text size="sm" truncate>
              {currentFlag.explanation}
            </Text>
            <Text size="xs" c="dimmed">
              {currentFlagIndex + 1}/{openFlags.length}
            </Text>
          </Group>
          <Group gap={6} wrap="nowrap">
            <Kbd size="xs">j</Kbd>
            <Kbd size="xs">k</Kbd>
            <Text size="xs" c="dimmed">
              flag
            </Text>
            <Kbd size="xs">a</Kbd>
            <Text size="xs" c="dimmed">
              approve
            </Text>
            <Kbd size="xs">s</Kbd>
            <Text size="xs" c="dimmed">
              skip
            </Text>
            <Kbd size="xs">e</Kbd>
            <Text size="xs" c="dimmed">
              edit
            </Text>
          </Group>
        </Group>
      ) : (
        <Group px="md" py={6} justify="space-between" className="document-view-flagbar">
          <OverlayLegend />
          <Group gap={6} wrap="nowrap">
            <Kbd size="xs">e</Kbd>
            <Text size="xs" c="dimmed">
              edit entity
            </Text>
            <Kbd size="xs">0</Kbd>
            <Text size="xs" c="dimmed">
              fit
            </Text>
          </Group>
        </Group>
      )}

      <SplitPane
        left={
          <ImageCanvas
            ref={canvasRef}
            documentId={document.id}
            regions={allRegions}
            flagsByRegionId={flagsByRegionId}
            correctedRegionIds={correctedRegionIds}
          />
        }
        right={<EntityPanel pages={document.pages} documentId={document.id} />}
      />

      {currentFlag ? (
        <Group px="md" py={6} className="document-view-legend">
          <OverlayLegend />
        </Group>
      ) : null}
    </Stack>
  );
}
