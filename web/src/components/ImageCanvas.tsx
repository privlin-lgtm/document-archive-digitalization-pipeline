import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type Ref,
} from "react";
import { ActionIcon, Group, Text, Tooltip } from "@mantine/core";
import { IconFocusCentered, IconMinus, IconPlus } from "@tabler/icons-react";
import { useDocumentImage } from "../hooks/useDocumentImage";
import { useSelectionStore } from "../state/selection";
import {
  confidenceOverlay,
  FLAG_TYPE_OVERLAY,
  HUMAN_CORRECTED_OVERLAY,
  HOVER_RING,
  SELECTION_RING,
} from "../utils/confidence";
import type { FlagType, RegionOut } from "../api/types";

const MIN_SCALE = 0.08;
const MAX_SCALE = 8;
const DRAG_THRESHOLD_PX = 4;
const VIEW_PAD = 16;

export interface ImageCanvasHandle {
  zoomBy: (factor: number) => void;
  fit: () => void;
}

interface ImageCanvasProps {
  documentId: string;
  regions: RegionOut[];
  flagsByRegionId: Map<string, FlagType>;
  correctedRegionIds: Set<string>;
  ref?: Ref<ImageCanvasHandle>;
}

interface ViewState {
  scale: number;
  x: number;
  y: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function fitView(container: DOMRect, imgW: number, imgH: number): ViewState {
  const availW = Math.max(1, container.width - VIEW_PAD * 2);
  const availH = Math.max(1, container.height - VIEW_PAD * 2);
  const scale = Math.min(availW / imgW, availH / imgH);
  return {
    scale,
    x: (container.width - imgW * scale) / 2,
    y: (container.height - imgH * scale) / 2,
  };
}

function zoomAt(view: ViewState, cx: number, cy: number, nextScale: number): ViewState {
  const scale = clamp(nextScale, MIN_SCALE, MAX_SCALE);
  const imgX = (cx - view.x) / view.scale;
  const imgY = (cy - view.y) / view.scale;
  return { scale, x: cx - imgX * scale, y: cy - imgY * scale };
}

function panRegionIntoView(view: ViewState, region: RegionOut, container: DOMRect): ViewState {
  const padding = 48;
  const rw = region.bbox_w * view.scale;
  const rh = region.bbox_h * view.scale;
  const rx = region.bbox_x * view.scale + view.x;
  const ry = region.bbox_y * view.scale + view.y;

  if (rw > container.width - padding * 2 || rh > container.height - padding * 2) {
    return {
      ...view,
      x: container.width / 2 - (region.bbox_x + region.bbox_w / 2) * view.scale,
      y: container.height / 2 - (region.bbox_y + region.bbox_h / 2) * view.scale,
    };
  }

  let { x, y } = view;
  if (rx < padding) x += padding - rx;
  if (ry < padding) y += padding - ry;
  if (rx + rw > container.width - padding) x -= rx + rw - (container.width - padding);
  if (ry + rh > container.height - padding) y -= ry + rh - (container.height - padding);
  return { ...view, x, y };
}

/**
 * Pan/zoom image viewer with clickable bounding-box overlays, kept in sync
 * with EntityPanel via the shared selection store.
 */
export function ImageCanvas({
  documentId,
  regions,
  flagsByRegionId,
  correctedRegionIds,
  ref,
}: ImageCanvasProps) {
  const { imageUrl, isLoading, error } = useDocumentImage(documentId, false);
  const selectedRegionId = useSelectionStore((s) => s.selectedRegionId);
  const selectedEntityId = useSelectionStore((s) => s.selectedEntityId);
  const hoveredRegionId = useSelectionStore((s) => s.hoveredRegionId);
  const focusGeneration = useSelectionStore((s) => s.focusGeneration);
  const selectRegion = useSelectionStore((s) => s.selectRegion);
  const selectEntity = useSelectionStore((s) => s.selectEntity);
  const hoverRegion = useSelectionStore((s) => s.hoverRegion);
  const clear = useSelectionStore((s) => s.clear);

  const viewportRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<ViewState>({ scale: 1, x: 0, y: 0 });
  const [view, setView] = useState<ViewState>({ scale: 1, x: 0, y: 0 });
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [smooth, setSmooth] = useState(false);
  const hasFitted = useRef(false);
  const drag = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);

  const raf = useRef<number | null>(null);
  const applyView = useCallback((next: ViewState, animate = false) => {
    viewRef.current = next;
    if (animate) {
      setSmooth(true);
      window.setTimeout(() => setSmooth(false), 160);
    }
    if (raf.current != null) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = null;
      setView(viewRef.current);
    });
  }, []);

  const fit = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport || naturalSize.width === 0) return;
    applyView(fitView(viewport.getBoundingClientRect(), naturalSize.width, naturalSize.height), true);
  }, [applyView, naturalSize.height, naturalSize.width]);

  const zoomBy = useCallback(
    (factor: number) => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      const rect = viewport.getBoundingClientRect();
      applyView(zoomAt(viewRef.current, rect.width / 2, rect.height / 2, viewRef.current.scale * factor), true);
    },
    [applyView],
  );

  useImperativeHandle(ref, () => ({ zoomBy, fit }), [fit, zoomBy]);

  useEffect(() => {
    hasFitted.current = false;
  }, [documentId, imageUrl]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || naturalSize.width === 0) return;
    const observer = new ResizeObserver(() => {
      if (!hasFitted.current) {
        applyView(fitView(viewport.getBoundingClientRect(), naturalSize.width, naturalSize.height));
        hasFitted.current = true;
      }
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [applyView, naturalSize.height, naturalSize.width]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    function onWheel(event: WheelEvent) {
      event.preventDefault();
      const node = viewportRef.current;
      if (!node) return;
      const rect = node.getBoundingClientRect();
      const factor = Math.exp(-event.deltaY * 0.0015);
      applyView(zoomAt(viewRef.current, event.clientX - rect.left, event.clientY - rect.top, viewRef.current.scale * factor));
    }

    viewport.addEventListener("wheel", onWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", onWheel);
  }, [applyView]);

  useEffect(() => {
    if (focusGeneration === 0 || naturalSize.width === 0 || !viewportRef.current) return;
    const regionId = useSelectionStore.getState().selectedRegionId;
    if (!regionId) return;
    const region = regions.find((item) => item.id === regionId);
    if (!region) return;
    applyView(panRegionIntoView(viewRef.current, region, viewportRef.current.getBoundingClientRect()), true);
  }, [applyView, focusGeneration, naturalSize.width, regions]);

  function handlePointerDown(event: ReactMouseEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    const target = event.target as HTMLElement;
    if (target.closest("[data-region-id]")) return;
    drag.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: viewRef.current.x,
      originY: viewRef.current.y,
      moved: false,
    };
  }

  function handlePointerMove(event: ReactMouseEvent<HTMLDivElement>) {
    if (!drag.current) return;
    const dx = event.clientX - drag.current.startX;
    const dy = event.clientY - drag.current.startY;
    if (!drag.current.moved && dx * dx + dy * dy < DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) return;
    drag.current.moved = true;
    if (viewportRef.current) viewportRef.current.style.cursor = "grabbing";
    applyView({ scale: viewRef.current.scale, x: drag.current.originX + dx, y: drag.current.originY + dy });
  }

  function handlePointerUp() {
    const wasClick = drag.current && !drag.current.moved;
    drag.current = null;
    if (viewportRef.current) viewportRef.current.style.cursor = "grab";
    if (wasClick) clear();
  }

  function handleOverlayClick(event: ReactMouseEvent<HTMLDivElement>, region: RegionOut) {
    event.stopPropagation();
    const isSelected = region.id === selectedRegionId;
    if (isSelected && !selectedEntityId) {
      selectRegion(null, "canvas");
      return;
    }
    if (region.entities.length === 1) {
      selectEntity(region.entities[0].id, region.id, "canvas");
      return;
    }
    selectRegion(region.id, "canvas");
  }

  const zoomPercent = Math.round(view.scale * 100);

  if (isLoading) {
    return (
      <div className="image-canvas-viewport image-canvas-status">
        <Text c="gray.4" size="sm">
          Loading scan…
        </Text>
      </div>
    );
  }
  if (error || !imageUrl) {
    return (
      <div className="image-canvas-viewport image-canvas-status">
        <Text c="gray.4" size="sm">
          Image unavailable
        </Text>
      </div>
    );
  }

  return (
    <div
      ref={viewportRef}
      className="image-canvas-viewport"
      onMouseDown={handlePointerDown}
      onMouseMove={handlePointerMove}
      onMouseUp={handlePointerUp}
      onMouseLeave={() => {
        drag.current = null;
        hoverRegion(null);
      }}
      onDoubleClick={(event) => {
        event.preventDefault();
        fit();
      }}
    >
      <div
        className="image-canvas-stage"
        style={{
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          transition: smooth ? "transform 140ms ease-out" : "none",
        }}
      >
        <img
          src={imageUrl}
          alt="Scanned document"
          draggable={false}
          onLoad={(event) => {
            const img = event.currentTarget;
            const size = { width: img.naturalWidth, height: img.naturalHeight };
            setNaturalSize(size);
            const viewport = viewportRef.current;
            if (viewport && !hasFitted.current) {
              applyView(fitView(viewport.getBoundingClientRect(), size.width, size.height));
              hasFitted.current = true;
            }
          }}
        />
        {naturalSize.width > 0 &&
          regions.map((region) => {
            const isSelected = region.id === selectedRegionId;
            const isHovered = region.id === hoveredRegionId;
            const flagType = flagsByRegionId.get(region.id);
            const isCorrected = correctedRegionIds.has(region.id);
            const overlay = flagType ? FLAG_TYPE_OVERLAY[flagType] : confidenceOverlay(region.confidence);
            const selectedEntity = region.entities.find((entity) => entity.id === selectedEntityId);
            const labelEntity = selectedEntity ?? (isHovered || isSelected ? region.entities[0] : undefined);
            const strokeW = (isSelected ? 3 : 2) / view.scale;
            const ringW = 3 / view.scale;
            const fill = isSelected
              ? overlay.fill.replace(/,\s*[\d.]+\)$/, ", 0.22)")
              : isHovered
                ? overlay.fill
                : isCorrected
                  ? HUMAN_CORRECTED_OVERLAY.fill
                  : "transparent";

            return (
              <div
                key={region.id}
                data-region-id={region.id}
                role="button"
                aria-pressed={isSelected}
                className="annotation-overlay"
                onClick={(event) => handleOverlayClick(event, region)}
                onMouseEnter={() => hoverRegion(region.id)}
                onMouseLeave={() => hoverRegion(null)}
                title={`${region.region_type} — ${Math.round(region.confidence * 100)}% confidence`}
                style={{
                  left: region.bbox_x,
                  top: region.bbox_y,
                  width: region.bbox_w,
                  height: region.bbox_h,
                  border: `${strokeW}px ${flagType ? "dashed" : "solid"} ${overlay.stroke}`,
                  backgroundColor: fill,
                  boxShadow: isSelected
                    ? `0 0 0 ${ringW}px #fff, 0 0 0 ${ringW * 2}px ${SELECTION_RING}`
                    : isHovered
                      ? `0 0 0 ${ringW}px ${HOVER_RING}`
                      : undefined,
                  zIndex: isSelected ? 3 : isHovered ? 2 : 1,
                }}
              >
                {isCorrected ? (
                  <span className="annotation-corrected-mark" style={{ width: 4 / view.scale }} />
                ) : null}
                {(isSelected || isHovered) && labelEntity ? (
                  <span
                    className="annotation-label"
                    style={{
                      transform: `translateY(-100%) scale(${1 / view.scale})`,
                      transformOrigin: "left bottom",
                    }}
                  >
                    {labelEntity.normalized_value ?? labelEntity.raw_text}
                  </span>
                ) : null}
              </div>
            );
          })}
      </div>

      <Group className="image-canvas-hud" gap={4} wrap="nowrap">
        <Tooltip label="Zoom out (−)">
          <ActionIcon size="sm" variant="filled" color="dark" onClick={() => zoomBy(1 / 1.15)} aria-label="Zoom out">
            <IconMinus size={14} />
          </ActionIcon>
        </Tooltip>
        <Text size="xs" c="gray.2" w={44} ta="center">
          {zoomPercent}%
        </Text>
        <Tooltip label="Zoom in (+)">
          <ActionIcon size="sm" variant="filled" color="dark" onClick={() => zoomBy(1.15)} aria-label="Zoom in">
            <IconPlus size={14} />
          </ActionIcon>
        </Tooltip>
        <Tooltip label="Fit to pane (0 / double-click)">
          <ActionIcon size="sm" variant="filled" color="dark" onClick={fit} aria-label="Fit to pane">
            <IconFocusCentered size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>
    </div>
  );
}
