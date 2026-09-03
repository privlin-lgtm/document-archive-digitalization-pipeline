"""Layout segmentation: split a scanned page into regions (paragraph, table,
signature, margin annotation, stamp) *before* OCR/entity extraction, so those
stages run per-region instead of on the whole page as one blob.

MVP approach: connected-component blob detection (merge nearby ink via
morphological dilation) plus position/geometry heuristics to classify each
merged blob. This is a clear extension point — swap `detect_regions` for a
call into a learned layout model (LayoutParser/Detectron2) later, keeping the
same `Region` output contract (bbox, region_type, reading_order) so
downstream OCR/extraction/annotation-UI code doesn't need to change.

`Region` is a plain, JSON-serializable dataclass (see `dataclasses.asdict`)
so it can be persisted alongside OCR text once the `regions` table lands
(Stage 5) — the annotation UI can then highlight "this date came from this
box on the page."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from ocr.preprocess import to_grayscale

BBox = tuple[int, int, int, int]  # (x, y, width, height)
RegionType = Literal["paragraph", "table", "signature", "margin_annotation", "stamp"]

# Ignore connected components smaller than this (scanner speckle/noise).
MIN_COMPONENT_AREA = 8

# Morphological dilation used to merge nearby ink (words -> lines -> blocks)
# into candidate regions. Tuned for ~300dpi-scale document scans.
DILATE_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 11))
DILATE_ITERATIONS = 2

# Candidate regions smaller than this (in pixels²) are dropped as noise.
MIN_REGION_AREA = 150

# Row/column clustering tolerance for grid detection and reading order,
# as a fraction of the component's own height.
ROW_CLUSTER_TOLERANCE = 0.6

# Region classification thresholds.
STAMP_MIN_FILL_RATIO = 0.35
STAMP_MAX_ASPECT_DEVIATION = 0.6  # |aspect_ratio - 1| must be below this
STAMP_MAX_SIZE_FRACTION = 0.25  # of page width/height
STAMP_MAX_COMPONENTS = 3

TABLE_MIN_ROWS = 3
TABLE_MIN_COLS = 2
TABLE_MIN_FILL_FRACTION = 0.6  # fraction of the row*col grid that has ink

SIGNATURE_MAX_HEIGHT_FRACTION = 0.12  # of page height
SIGNATURE_MAX_WIDTH_FRACTION = 0.5  # of page width
SIGNATURE_BOTTOM_ZONE_FRACTION = 0.25  # region must start in bottom N% of page

MARGIN_ZONE_FRACTION = 0.12  # outer page-width fraction treated as margin
MARGIN_MAX_AREA_FRACTION = 0.05  # of full page area


@dataclass
class Component:
    bbox: BBox
    area: int
    centroid: tuple[float, float]


@dataclass
class Region:
    bbox: BBox
    region_type: RegionType
    reading_order: int
    confidence: float


def _binarize_ink(gray: np.ndarray) -> np.ndarray:
    """Otsu-threshold to a 0/255 mask where ink (text/marks) is 255."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def _connected_components(binary: np.ndarray, min_area: int = MIN_COMPONENT_AREA) -> list[Component]:
    num_labels, _, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    components = []
    for i in range(1, num_labels):  # skip background label 0
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        bbox = (
            int(stats[i, cv2.CC_STAT_LEFT]),
            int(stats[i, cv2.CC_STAT_TOP]),
            int(stats[i, cv2.CC_STAT_WIDTH]),
            int(stats[i, cv2.CC_STAT_HEIGHT]),
        )
        components.append(Component(bbox=bbox, area=area, centroid=tuple(centroids[i])))
    return components


def _candidate_region_boxes(binary: np.ndarray) -> list[BBox]:
    """Merge nearby ink into candidate region blobs via dilation."""
    dilated = cv2.dilate(binary, DILATE_KERNEL, iterations=DILATE_ITERATIONS)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < MIN_REGION_AREA:
            continue
        boxes.append((x, y, w, h))
    return boxes


def _cluster_1d(centers: list[float], tolerance: float) -> list[int]:
    """Assign each center to a cluster index, scanning in sorted order and
    starting a new cluster whenever the gap to the previous center exceeds
    `tolerance`. Returns cluster indices aligned to the *original* order of
    `centers`.
    """
    order = sorted(range(len(centers)), key=lambda i: centers[i])
    cluster_of = [0] * len(centers)
    current_cluster = 0
    last_center = None
    for i in order:
        center = centers[i]
        if last_center is not None and (center - last_center) > tolerance:
            current_cluster += 1
        cluster_of[i] = current_cluster
        last_center = center
    return cluster_of


def _components_in_bbox(components: list[Component], bbox: BBox) -> list[Component]:
    x, y, w, h = bbox
    in_bbox = []
    for comp in components:
        cx, cy = comp.centroid
        if x <= cx <= x + w and y <= cy <= y + h:
            in_bbox.append(comp)
    return in_bbox


def classify_region_type(
    bbox: BBox, sub_components: list[Component], page_shape: tuple[int, int]
) -> tuple[RegionType, float]:
    """Classify a candidate region using position/geometry heuristics.

    Order matters: more specific patterns (stamp, table, signature, margin
    annotation) are checked before falling back to the generic "paragraph".
    """
    page_h, page_w = page_shape
    x, y, w, h = bbox
    area = w * h
    page_area = page_h * page_w
    aspect_ratio = w / h if h else 0.0

    ink_area = sum(c.area for c in sub_components)
    fill_ratio = ink_area / area if area else 0.0
    num_components = len(sub_components)

    # Stamp: compact, dense, roughly square, not part of a larger grid.
    if (
        fill_ratio >= STAMP_MIN_FILL_RATIO
        and abs(aspect_ratio - 1.0) <= STAMP_MAX_ASPECT_DEVIATION
        and w <= page_w * STAMP_MAX_SIZE_FRACTION
        and h <= page_h * STAMP_MAX_SIZE_FRACTION
        and num_components <= STAMP_MAX_COMPONENTS
    ):
        return "stamp", 0.85

    # Table: sub-components form a regular row/column grid (ledger cells).
    if num_components >= TABLE_MIN_ROWS * TABLE_MIN_COLS:
        heights = [c.bbox[3] for c in sub_components]
        avg_height = sum(heights) / len(heights)
        row_tolerance = max(avg_height * ROW_CLUSTER_TOLERANCE, 1.0)
        row_clusters = _cluster_1d([c.centroid[1] for c in sub_components], row_tolerance)
        col_clusters = _cluster_1d([c.centroid[0] for c in sub_components], row_tolerance)
        row_count = len(set(row_clusters))
        col_count = len(set(col_clusters))
        grid_capacity = row_count * col_count
        if (
            row_count >= TABLE_MIN_ROWS
            and col_count >= TABLE_MIN_COLS
            and num_components >= grid_capacity * TABLE_MIN_FILL_FRACTION
        ):
            return "table", 0.8

    # Signature: short, narrow block near the bottom of the page.
    if (
        y >= page_h * (1 - SIGNATURE_BOTTOM_ZONE_FRACTION)
        and h <= page_h * SIGNATURE_MAX_HEIGHT_FRACTION
        and w <= page_w * SIGNATURE_MAX_WIDTH_FRACTION
    ):
        return "signature", 0.6

    # Margin annotation: small block sitting in the outer left/right margin.
    in_left_margin = (x + w) <= page_w * MARGIN_ZONE_FRACTION
    in_right_margin = x >= page_w * (1 - MARGIN_ZONE_FRACTION)
    if (in_left_margin or in_right_margin) and area <= page_area * MARGIN_MAX_AREA_FRACTION:
        return "margin_annotation", 0.6

    return "paragraph", 0.5


def _reading_order_sort(boxes: list[BBox]) -> list[int]:
    """Return indices of `boxes` sorted top-to-bottom, left-to-right.

    Boxes are grouped into visual rows (vertically overlapping y-ranges),
    ordered top to bottom; within a row, boxes are ordered left to right.
    """
    if not boxes:
        return []

    order_by_top = sorted(range(len(boxes)), key=lambda i: boxes[i][1])
    rows: list[list[int]] = []
    row_y_ranges: list[tuple[int, int]] = []

    for i in order_by_top:
        _x, y, _w, h = boxes[i]
        y0, y1 = y, y + h
        placed = False
        for row_idx, (ry0, ry1) in enumerate(row_y_ranges):
            overlap = min(y1, ry1) - max(y0, ry0)
            if overlap > 0.5 * min(h, ry1 - ry0):
                rows[row_idx].append(i)
                row_y_ranges[row_idx] = (min(ry0, y0), max(ry1, y1))
                placed = True
                break
        if not placed:
            rows.append([i])
            row_y_ranges.append((y0, y1))

    ordered_indices: list[int] = []
    for row in rows:
        row.sort(key=lambda i: boxes[i][0])
        ordered_indices.extend(row)
    return ordered_indices


def detect_regions(image: np.ndarray) -> list[Region]:
    """Detect and classify layout regions on a scanned page.

    Returns regions in reading order (top-to-bottom, left-to-right), each
    tagged with a bounding box and a region type (paragraph/table/signature/
    margin_annotation/stamp).
    """
    gray = to_grayscale(image)
    binary = _binarize_ink(gray)
    components = _connected_components(binary)
    candidate_boxes = _candidate_region_boxes(binary)

    classified: list[tuple[BBox, RegionType, float]] = []
    for bbox in candidate_boxes:
        sub_components = _components_in_bbox(components, bbox)
        if not sub_components:
            continue
        region_type, confidence = classify_region_type(bbox, sub_components, gray.shape)
        classified.append((bbox, region_type, confidence))

    order = _reading_order_sort([c[0] for c in classified])
    regions = [
        Region(bbox=classified[i][0], region_type=classified[i][1], reading_order=order_idx, confidence=classified[i][2])
        for order_idx, i in enumerate(order)
    ]
    return regions
