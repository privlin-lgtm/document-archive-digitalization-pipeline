-- Seeds a synthetic archive dataset for exercising stage 5's schema at
-- scale (full-text search, BRIN vs B-tree on entities.date_value, trigram
-- fuzzy search). Run with:
--   docker compose exec -T db psql -U postgres -d document_archive -f - < scripts/seed_synthetic_data.sql
--
-- documents.upload_time and entities.date_value are both generated so they
-- trend upward with each row's position in the bulk insert (i.e. with
-- physical/insertion order), simulating an archive digitized in roughly
-- chronological order of the events it describes — the assumption the
-- BRIN index on entities.date_value depends on (see the migration comment
-- and queries.py's EXPLAIN ANALYZE walkthrough).

BEGIN;

-- A handful of hand-crafted rows with realistic, *guaranteed* co-occurring
-- entities, so the example queries in queries.py have real matches to
-- demonstrate against (not just statistical noise from the bulk data below).
WITH seed_doc AS (
    INSERT INTO documents (id, filename, upload_time, status, raw_image_path)
    VALUES (gen_random_uuid(), 'ledger_1897_smith_bombay.png', '1897-03-05', 'indexed', '/data/documents/seed1.png')
    RETURNING id, upload_time
),
seed_page AS (
    INSERT INTO pages (id, document_id, page_number, full_text, created_at)
    SELECT gen_random_uuid(), seed_doc.id, 1,
           'John Smith paid three pounds twelve shillings sixpence to the estate on the 3rd day of March, 1897 in Bombay.',
           seed_doc.upload_time
    FROM seed_doc
    RETURNING id, document_id, created_at
),
seed_region AS (
    INSERT INTO regions (id, page_id, bbox_x, bbox_y, bbox_w, bbox_h, region_type, reading_order, confidence, created_at)
    SELECT gen_random_uuid(), seed_page.id, 40, 60, 500, 90, 'paragraph', 0, 0.92, seed_page.created_at
    FROM seed_page
    RETURNING id, created_at
),
seed_ocr AS (
    INSERT INTO ocr_results (id, region_id, engine, text, confidence, status, notes, created_at)
    SELECT gen_random_uuid(), seed_region.id, 'tesseract',
           'John Smith paid £3 12s 6d to the estate on the 3rd day of March, 1897 in Bombay.',
           94.5, 'ok', NULL, seed_region.created_at
    FROM seed_region
)
INSERT INTO entities (id, region_id, entity_type, raw_text, normalized_value, confidence, start_char, end_char, date_value, amount_value, amount_currency, created_at)
SELECT gen_random_uuid(), seed_region.id, v.entity_type, v.raw_text, v.normalized_value, v.confidence,
       v.start_char, v.end_char, v.date_value, v.amount_value, v.amount_currency, seed_region.created_at
FROM seed_region
CROSS JOIN (VALUES
    ('person'::entity_type, 'John Smith', 'John Smith', 0.7, 0, 10, NULL::date, NULL::numeric, NULL::text),
    ('amount'::entity_type, '£3 12s 6d', 'GBP 3.62', 0.9, 16, 25, NULL::date, 3.62::numeric, 'GBP'),
    ('date'::entity_type, 'the 3rd day of March, 1897', '1897-03-03', 0.92, 43, 69, '1897-03-03'::date, NULL::numeric, NULL::text),
    ('location'::entity_type, 'Bombay', 'Mumbai', 0.95, 73, 79, NULL::date, NULL::numeric, NULL::text)
) AS v(entity_type, raw_text, normalized_value, confidence, start_char, end_char, date_value, amount_value, amount_currency);

-- A second seed document: same person (slightly OCR-garbled spelling) and
-- a different, larger amount a few weeks later — exercises fuzzy person
-- search and the "amounts over $X between two dates" query together.
WITH seed_doc2 AS (
    INSERT INTO documents (id, filename, upload_time, status, raw_image_path)
    VALUES (gen_random_uuid(), 'ledger_1897_smyth_calcutta.png', '1897-03-20', 'indexed', '/data/documents/seed2.png')
    RETURNING id, upload_time
),
seed_page2 AS (
    INSERT INTO pages (id, document_id, page_number, full_text, created_at)
    SELECT gen_random_uuid(), seed_doc2.id, 1,
           'John Smyth received one hundred twenty dollars on the 20th of March, 1897 from Calcutta.',
           seed_doc2.upload_time
    FROM seed_doc2
    RETURNING id, created_at
),
seed_region2 AS (
    INSERT INTO regions (id, page_id, bbox_x, bbox_y, bbox_w, bbox_h, region_type, reading_order, confidence, created_at)
    SELECT gen_random_uuid(), seed_page2.id, 40, 60, 500, 90, 'paragraph', 0, 0.88, seed_page2.created_at
    FROM seed_page2
    RETURNING id, created_at
),
seed_ocr2 AS (
    INSERT INTO ocr_results (id, region_id, engine, text, confidence, status, notes, created_at)
    SELECT gen_random_uuid(), seed_region2.id, 'tesseract',
           'John Smyth received $120.00 on the 20th of March, 1897 from Calcutta.',
           89.0, 'ok', NULL, seed_region2.created_at
    FROM seed_region2
)
INSERT INTO entities (id, region_id, entity_type, raw_text, normalized_value, confidence, start_char, end_char, date_value, amount_value, amount_currency, created_at)
SELECT gen_random_uuid(), seed_region2.id, v.entity_type, v.raw_text, v.normalized_value, v.confidence,
       v.start_char, v.end_char, v.date_value, v.amount_value, v.amount_currency, seed_region2.created_at
FROM seed_region2
CROSS JOIN (VALUES
    ('person'::entity_type, 'John Smyth', 'John Smyth', 0.7, 0, 10, NULL::date, NULL::numeric, NULL::text),
    ('amount'::entity_type, '$120.00', 'USD 120.00', 0.95, 33, 40, NULL::date, 120.00::numeric, 'USD'),
    ('date'::entity_type, 'the 20th of March, 1897', '1897-03-20', 0.92, 44, 67, '1897-03-20'::date, NULL::numeric, NULL::text),
    ('location'::entity_type, 'Calcutta', 'Kolkata', 0.95, 73, 81, NULL::date, NULL::numeric, NULL::text)
) AS v(entity_type, raw_text, normalized_value, confidence, start_char, end_char, date_value, amount_value, amount_currency);

COMMIT;

-- Bulk synthetic data: 5,000 documents, ~3 pages each, ~4 regions per page,
-- 1 ocr_result per region, ~3 entities per region.
BEGIN;

CREATE TEMP TABLE tmp_documents AS
SELECT
    gen_random_uuid() AS id,
    'archive_doc_' || i || '.png' AS filename,
    -- upload_time trends upward with row position (i), with local jitter —
    -- simulates roughly-chronological batch processing, not strict order.
    (timestamp '1750-01-01' + (i * interval '1 day') + ((random() * 60)::int * interval '1 day')) AS upload_time,
    'indexed'::document_status AS status,
    '/data/documents/archive_doc_' || i || '.png' AS raw_image_path
FROM generate_series(1, 5000) AS s(i);

INSERT INTO documents (id, filename, upload_time, status, raw_image_path)
SELECT id, filename, upload_time, status, raw_image_path FROM tmp_documents;

CREATE TEMP TABLE tmp_pages AS
SELECT
    gen_random_uuid() AS id,
    d.id AS document_id,
    p.page_num AS page_number,
    'Synthetic OCR text for page ' || p.page_num || ' of ' || d.filename ||
        '. Recorded ' || to_char(d.upload_time, 'FMMonth DD, YYYY') || '.' AS full_text,
    d.upload_time AS created_at
FROM tmp_documents d
CROSS JOIN generate_series(1, 3) AS p(page_num);

INSERT INTO pages (id, document_id, page_number, full_text, created_at)
SELECT id, document_id, page_number, full_text, created_at FROM tmp_pages;

CREATE TEMP TABLE tmp_regions AS
SELECT
    gen_random_uuid() AS id,
    pg.id AS page_id,
    (r.i * 60) % 800 AS bbox_x,
    (r.i * 45) % 1000 AS bbox_y,
    300 AS bbox_w,
    30 AS bbox_h,
    (ARRAY['paragraph', 'table', 'signature', 'margin_annotation', 'stamp'])[1 + (r.i % 5)]::region_type AS region_type,
    r.i AS reading_order,
    (0.5 + random() * 0.5)::real AS confidence,
    pg.created_at AS created_at
FROM tmp_pages pg
CROSS JOIN generate_series(0, 3) AS r(i);

INSERT INTO regions (id, page_id, bbox_x, bbox_y, bbox_w, bbox_h, region_type, reading_order, confidence, created_at)
SELECT id, page_id, bbox_x, bbox_y, bbox_w, bbox_h, region_type, reading_order, confidence, created_at FROM tmp_regions;

INSERT INTO ocr_results (id, region_id, engine, text, confidence, status, notes, created_at)
SELECT
    gen_random_uuid(),
    reg.id,
    'tesseract',
    'Synthetic OCR text for region ' || reg.id || '.',
    (60 + random() * 40)::real,
    'ok'::ocr_result_status,
    NULL,
    reg.created_at
FROM tmp_regions reg;

-- 4 entities per region, one of each type (~240,000 rows), row-numbered
-- globally so date_value can be made to trend with insertion order.
CREATE TEMP TABLE tmp_entity_seeds AS
SELECT
    row_number() OVER () AS seq,
    (SELECT count(*) FROM tmp_regions) * 4 AS total_seq,
    reg.id AS region_id,
    reg.created_at AS created_at,
    e.slot AS slot
FROM tmp_regions reg
CROSS JOIN generate_series(0, 3) AS e(slot);

INSERT INTO entities (
    id, region_id, entity_type, raw_text, normalized_value, confidence,
    start_char, end_char, date_value, amount_value, amount_currency, created_at
)
SELECT
    gen_random_uuid(),
    region_id,
    entity_type,
    CASE WHEN entity_type = 'date' THEN 'the ' || to_char(computed_date, 'FMDD "day of" FMMonth, YYYY') ELSE raw_text_base END AS raw_text,
    CASE WHEN entity_type = 'date' THEN to_char(computed_date, 'YYYY-MM-DD') ELSE normalized_value END AS normalized_value,
    (0.5 + random() * 0.45)::real AS confidence,
    0 AS start_char,
    length(CASE WHEN entity_type = 'date' THEN to_char(computed_date, 'FMDD "day of" FMMonth, YYYY') ELSE raw_text_base END) AS end_char,
    CASE WHEN entity_type = 'date' THEN computed_date ELSE NULL END AS date_value,
    amount_value,
    amount_currency,
    created_at
FROM (
    SELECT
        region_id,
        created_at,
        (ARRAY['person', 'date', 'location', 'amount'])[1 + slot]::entity_type AS entity_type,
        -- Linear day offset spanning ~250 years (matching the year range
        -- named in the 'date' raw_text below) across the full row sequence,
        -- plus jitter — this is the insertion-order correlation the BRIN
        -- index on date_value depends on.
        (date '1750-01-01' + ((seq::float / total_seq * 250 * 365)::int * interval '1 day')
            + ((random() * 30)::int * interval '1 day'))::date AS computed_date,
        CASE slot
            WHEN 0 THEN (ARRAY['John Smith', 'Mary Jones', 'Robert Williams', 'Elizabeth Carter', 'Thomas Brown'])[1 + (seq % 5)]
            WHEN 2 THEN (ARRAY['Bombay', 'Calcutta', 'Constantinople', 'Peking', 'Rhodesia', 'London', 'Boston'])[1 + (seq % 7)]
            WHEN 3 THEN '$' || (1 + (seq % 5000))::text || '.00'
            ELSE NULL
        END AS raw_text_base,
        CASE slot
            WHEN 0 THEN (ARRAY['John Smith', 'Mary Jones', 'Robert Williams', 'Elizabeth Carter', 'Thomas Brown'])[1 + (seq % 5)]
            WHEN 2 THEN (ARRAY['Mumbai', 'Kolkata', 'Istanbul', 'Beijing', 'Zimbabwe', 'London', 'Boston'])[1 + (seq % 7)]
            WHEN 3 THEN 'USD ' || (1 + (seq % 5000))::text || '.00'
            ELSE NULL
        END AS normalized_value,
        CASE slot
            WHEN 3 THEN (1 + (seq % 5000))::numeric
            ELSE NULL
        END AS amount_value,
        CASE slot
            WHEN 3 THEN 'USD'
            ELSE NULL
        END AS amount_currency
    FROM tmp_entity_seeds
) AS generated;

COMMIT;

-- Refresh planner statistics so EXPLAIN ANALYZE reflects the new data.
ANALYZE documents;
ANALYZE pages;
ANALYZE regions;
ANALYZE ocr_results;
ANALYZE entities;

SELECT 'documents' AS table_name, count(*) FROM documents
UNION ALL SELECT 'pages', count(*) FROM pages
UNION ALL SELECT 'regions', count(*) FROM regions
UNION ALL SELECT 'ocr_results', count(*) FROM ocr_results
UNION ALL SELECT 'entities', count(*) FROM entities;
