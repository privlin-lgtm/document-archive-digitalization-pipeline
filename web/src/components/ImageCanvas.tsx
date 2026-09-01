import { useRef, useState, type MouseEvent, type WheelEvent } from "react";
import { Center, Loader, Text } from "@mantine/core";
import { useDocumentImage } from "../hooks/useDocumentImage";
import { useSelectionStore } from "../state/selection";
import { confidenceColor } from "../utils/confidence";
import type { RegionOut } from "../api/types";

const ZOOM_STEP = 0.1;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;

interface ImageCanvasProps {
  documentId: string;
  regions: RegionOut[];
  /** region ids that have at least one open review flag, for a distinct outline */
  flaggedRegionIds: Set<string>;
}

/**
 * Pan/zoom image viewer with clickable bounding-box overlays, kept in sync
 * with EntityPanel via the shared selection store. The pan/zoom
 * interaction (drag feel, zoom increments, snapping) is a first pass —
 * exactly the kind of "look at it, nudge it" tuning the stage 8 spec
 * hands off to an interactive pass.
 */
export function ImageCanvas({ documentId, regions, flaggedRegionIds }: ImageCanvasProps) {
  const { imageUrl, isLoading, error } = useDocumentImage(documentId, false);
  const { selectedRegionId, selectRegion } = useSelectionStore();

  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const dragState = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null);

  function handleWheel(event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const delta = event.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z + delta)));
  }

  function handleMouseDown(event: MouseEvent<HTMLDivElement>) {
    dragState.current = { startX: event.clientX, startY: event.clientY, originX: offset.x, originY: offset.y };
  }

  function handleMouseMove(event: MouseEvent<HTMLDivElement>) {
    if (!dragState.current) return;
    const dx = event.clientX - dragState.current.startX;
    const dy = event.clientY - dragState.current.startY;
    setOffset({ x: dragState.current.originX + dx, y: dragState.current.originY + dy });
  }

  function handleMouseUp() {
    dragState.current = null;
  }

  function resetView() {
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  }

  if (isLoading) {
    return (
      <Center h="100%">
        <Loader />
      </Center>
    );
  }
  if (error || !imageUrl) {
    return (
      <Center h="100%">
        <Text c="dimmed">Image unavailable</Text>
      </Center>
    );
  }

  return (
    <div
      style={{ width: "100%", height: "100%", overflow: "hidden", cursor: "grab", position: "relative" }}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onDoubleClick={resetView}
    >
      <div
        style={{
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
          transformOrigin: "top left",
          position: "relative",
          display: "inline-block",
        }}
      >
        <img
          src={imageUrl}
          alt="Scanned document"
          draggable={false}
          onLoad={(event) => {
            const img = event.currentTarget;
            setNaturalSize({ width: img.naturalWidth, height: img.naturalHeight });
          }}
          style={{ display: "block", userSelect: "none" }}
        />
        {naturalSize.width > 0 &&
          regions.map((region) => {
            const isSelected = region.id === selectedRegionId;
            const isFlagged = flaggedRegionIds.has(region.id);
            return (
              <div
                key={region.id}
                onClick={(event) => {
                  event.stopPropagation();
                  selectRegion(isSelected ? null : region.id);
                }}
                title={`${region.region_type} — ${Math.round(region.confidence * 100)}% confidence`}
                style={{
                  position: "absolute",
                  left: region.bbox_x,
                  top: region.bbox_y,
                  width: region.bbox_w,
                  height: region.bbox_h,
                  border: `2px ${isFlagged ? "dashed" : "solid"} ${
                    isSelected ? "#1c7ed6" : confidenceColor(region.confidence)
                  }`,
                  backgroundColor: isSelected ? "rgba(28, 126, 214, 0.15)" : "transparent",
                  cursor: "pointer",
                  boxSizing: "border-box",
                }}
              />
            );
          })}
      </div>
    </div>
  );
}
