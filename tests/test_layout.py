import cv2
import numpy as np

from ocr.layout import detect_regions

PAGE_W, PAGE_H = 1000, 1400


def blank_page(width: int = PAGE_W, height: int = PAGE_H) -> np.ndarray:
    return np.full((height, width), 255, dtype=np.uint8)


def draw_rect(page: np.ndarray, x: int, y: int, w: int, h: int) -> None:
    cv2.rectangle(page, (x, y), (x + w, y + h), 0, thickness=-1)


class TestReadingOrder:
    def test_three_blocks_ordered_top_to_bottom_left_to_right(self):
        page = blank_page()
        # Top-left, top-right (same row), then bottom-left.
        draw_rect(page, 100, 100, 200, 60)
        draw_rect(page, 700, 100, 200, 60)
        draw_rect(page, 100, 900, 200, 60)

        regions = detect_regions(page)

        assert len(regions) == 3
        ordered = sorted(regions, key=lambda r: r.reading_order)
        assert [r.reading_order for r in ordered] == [0, 1, 2]
        # top-left first, top-right second, bottom-left last
        assert ordered[0].bbox[0] < ordered[1].bbox[0]
        assert ordered[0].bbox[1] < ordered[2].bbox[1]
        assert ordered[1].bbox[1] < ordered[2].bbox[1]


class TestParagraphClassification:
    def test_stacked_text_lines_classified_as_paragraph(self):
        page = blank_page()
        y = 300
        for _ in range(5):
            draw_rect(page, 100, y, 600, 20)
            y += 30  # 10px gap between lines

        regions = detect_regions(page)

        assert len(regions) == 1
        assert regions[0].region_type == "paragraph"


class TestTableClassification:
    def test_grid_of_cells_classified_as_table(self):
        page = blank_page()
        for row in range(4):
            for col in range(3):
                x = 100 + col * 55
                y = 300 + row * 30
                draw_rect(page, x, y, 40, 20)

        regions = detect_regions(page)

        assert len(regions) == 1
        assert regions[0].region_type == "table"


class TestSignatureClassification:
    def test_small_block_near_bottom_classified_as_signature(self):
        page = blank_page()
        draw_rect(page, 700, 1300, 150, 25)

        regions = detect_regions(page)

        assert len(regions) == 1
        assert regions[0].region_type == "signature"


class TestStampClassification:
    def test_compact_dense_circle_classified_as_stamp(self):
        page = blank_page()
        cv2.circle(page, (150, 150), 40, 0, thickness=-1)

        regions = detect_regions(page)

        assert len(regions) == 1
        assert regions[0].region_type == "stamp"


class TestMarginAnnotationClassification:
    def test_small_block_in_left_margin_classified_as_margin_annotation(self):
        page = blank_page()
        draw_rect(page, 10, 500, 60, 15)

        regions = detect_regions(page)

        assert len(regions) == 1
        assert regions[0].region_type == "margin_annotation"


class TestMixedPage:
    def test_letter_like_page_detects_multiple_region_types_in_order(self):
        """Header paragraph, body paragraph, and a bottom-right signature —
        the classic letter layout (header/body/signature) from the spec.
        """
        page = blank_page()
        draw_rect(page, 100, 100, 500, 20)  # header line

        y = 300
        for _ in range(4):
            draw_rect(page, 100, y, 700, 20)
            y += 30  # body paragraph

        draw_rect(page, 700, 1300, 150, 25)  # signature, bottom-right

        regions = detect_regions(page)

        assert len(regions) == 3
        ordered = sorted(regions, key=lambda r: r.reading_order)
        types_in_order = [r.region_type for r in ordered]
        assert types_in_order == ["paragraph", "paragraph", "signature"]
        # header must come before body, body before signature
        assert ordered[0].bbox[1] < ordered[1].bbox[1] < ordered[2].bbox[1]

    def test_all_regions_have_json_serializable_bbox_and_valid_reading_order(self):
        page = blank_page()
        draw_rect(page, 100, 100, 200, 60)
        draw_rect(page, 700, 1300, 150, 25)

        regions = detect_regions(page)

        reading_orders = sorted(r.reading_order for r in regions)
        assert reading_orders == list(range(len(regions)))
        for region in regions:
            assert len(region.bbox) == 4
            assert all(isinstance(v, int) for v in region.bbox)
            assert 0.0 <= region.confidence <= 1.0
