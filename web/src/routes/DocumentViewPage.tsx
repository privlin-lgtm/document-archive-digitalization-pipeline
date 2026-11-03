import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Group, Kbd, Loader, Stack, Text } from "@mantine/core";
import { useHotkeys } from "@mantine/hooks";
import { useDocument, useReprocessDocument } from "../hooks/useDocuments";
import { useUpdateReviewFlag } from "../hooks/useReviewFlags";
import { ImageCanvas, type ImageCanvasHandle } from "../components/ImageCanvas";
import { EntityPanel } from "../components/EntityPanel";
import { FlagBadge } from "../components/FlagBadge";
import { SplitPane } from "../components/SplitPane";
import { PageSwitcher } from "../components/PageSwitcher";
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
  const reprocess = useReprocessDocument(documentId ?? "");
  const updateFlag = useUpdateReviewFlag();
  const canvasRef = useRef<ImageCanvasHandle>(null);
  const initializedDocumentId = useRef<string | null>(null);

  const selectedFlagId = useSelectionStore((s) => s.selectedFlagId);
  const editingEntityId = useSelectionStore((s) => s.editingEntityId);
  const selectFlag = useSelectionStore((s) => s.selectFlag);
  const setEditingEntityId = useSelectionStore((s) => s.setEditingEntityId);
  const clear = useSelectionStore((s) => s.clear);

  // Keyed by document so navigating to another document starts on page 1
  // without a reset effect.
  const [pageState, setPageState] = useState<{ documentId?: string; page: number }>({ page: 1 });
  const pageNumber = pageState.documentId === documentId ? pageState.page : 1;

  const sortedPages = useMemo(
    () => [...(document?.pages ?? [])].sort((a, b) => a.page_number - b.page_number),
    [document],
  );
  const currentPage = sortedPages.find((page) => page.page_number === pageNumber) ?? sortedPages[0];
  const pageNumberByRegionId = useMemo(() => {
    const map = new Map<string, number>();
    for (const page of sortedPages) for (const region of page.regions) map.set(region.id, page.page_number);
    return map;
  }, [sortedPages]);
  const pageNumberByPageId = useMemo(
    () => new Map(sortedPages.map((page) => [page.id, page.page_number])),
    [sortedPages],
  );
  const allRegions = useMemo(() => sortedPages.flatMap((page) => page.regions), [sortedPages]);
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

  const openFlagsByPage = useMemo(() => {
    const map = new Map<number, number>();
    for (const flag of openFlags) {
      const number = flag.page_id ? pageNumberByPageId.get(flag.page_id) : undefined;
      if (number != null) map.set(number, (map.get(number) ?? 0) + 1);
    }
    return map;
  }, [openFlags, pageNumberByPageId]);

  const currentFlag: ReviewFlagOut | undefined =
    openFlags.find((flag) => flag.id === selectedFlagId) ?? openFlags[0];
  const currentFlagIndex = currentFlag ? openFlags.findIndex((flag) => flag.id === currentFlag.id) : -1;

  useEffect(() => {
    initializedDocumentId.current = null;
    return () => clear();
  }, [clear, documentId]);

  // Follow the selection across pages: picking a flag or an entity on
  // another page (from the flag bar, keyboard or entity panel) shows that
  // page, since the canvas only draws the current page's regions.
  useEffect(
    () =>
      useSelectionStore.subscribe((state, prev) => {
        if (state.selectedRegionId === prev.selectedRegionId && state.selectedFlagId === prev.selectedFlagId) return;
        const regionPage = state.selectedRegionId ? pageNumberByRegionId.get(state.selectedRegionId) : undefined;
        const flag = state.selectedFlagId ? document?.flags.find((f) => f.id === state.selectedFlagId) : undefined;
        const flagPage = flag?.page_id ? pageNumberByPageId.get(flag.page_id) : undefined;
        const target = regionPage ?? flagPage;
        if (target != null) setPageState({ documentId, page: target });
      }),
    [document, documentId, pageNumberByPageId, pageNumberByRegionId],
  );

  function goToPage(next: number) {
    if (sortedPages.length === 0) return;
    const clamped = Math.min(sortedPages.length, Math.max(1, next));
    if (clamped === (currentPage?.page_number ?? 1)) return;
    // Drop a selection that lives on the page being left.
    clear();
    setPageState({ documentId, page: sortedPages[clamped - 1].page_number });
  }

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
    if (!currentFlag || updateFlag.isPending) return;
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

  // Bare single-letter shortcuts collide with screen readers' own browse-mode
  // quick-nav keys (WCAG 2.1.4) -- requiring Alt keeps the shortcuts without
  // stealing keys assistive tech already uses. Arrow keys/Escape are left
  // bare: they aren't printable-character shortcuts, so 2.1.4 doesn't apply.
  useHotkeys([
    ["alt+j", () => moveFlag(1), { preventDefault: true }],
    ["ArrowDown", () => moveFlag(1), { preventDefault: true }],
    ["alt+k", () => moveFlag(-1), { preventDefault: true }],
    ["ArrowUp", () => moveFlag(-1), { preventDefault: true }],
    ["alt+a", () => resolveCurrent("resolved")],
    ["alt+r", () => resolveCurrent("resolved")],
    ["alt+x", () => resolveCurrent("dismissed")],
    ["alt+s", () => moveFlag(1), { preventDefault: true }],
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
    ["PageDown", () => goToPage((currentPage?.page_number ?? 1) + 1), { preventDefault: true }],
    ["PageUp", () => goToPage((currentPage?.page_number ?? 1) - 1), { preventDefault: true }],
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
          <PageSwitcher
            page={currentPage?.page_number ?? 1}
            pageCount={sortedPages.length}
            onChange={goToPage}
            openFlagsByPage={openFlagsByPage}
          />
          <Badge variant="light">{document.status}</Badge>
          {document.status === "enqueue_failed" || document.status === "error" ? (
            <Button size="xs" variant="light" loading={reprocess.isPending} onClick={() => reprocess.mutate()}>
              Reprocess
            </Button>
          ) : null}
          {document.error_message ? (
            <Text size="xs" c="red" lineClamp={1}>
              {document.error_message}
            </Text>
          ) : null}
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
            <Kbd size="xs">Alt+J</Kbd>
            <Kbd size="xs">Alt+K</Kbd>
            <Text size="xs" c="dimmed">
              flag
            </Text>
            <Kbd size="xs">Alt+A</Kbd>
            <Text size="xs" c="dimmed">
              approve
            </Text>
            <Kbd size="xs">Alt+S</Kbd>
            <Text size="xs" c="dimmed">
              skip
            </Text>
            <Kbd size="xs">Alt+E</Kbd>
            <Text size="xs" c="dimmed">
              edit
            </Text>
          </Group>
        </Group>
      ) : (
        <Group px="md" py={6} justify="space-between" className="document-view-flagbar">
          <OverlayLegend />
          <Group gap={6} wrap="nowrap">
            <Kbd size="xs">Alt+E</Kbd>
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
            page={currentPage?.page_number ?? 1}
            regions={currentPage?.regions ?? []}
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
