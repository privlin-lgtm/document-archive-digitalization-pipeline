// Mirrors review_api/schemas.py. Keep these in sync by hand for now — a
// later pass could generate this file from the backend's OpenAPI schema.

export type EntityType = "person" | "date" | "location" | "amount";
export type RegionType = "paragraph" | "table" | "signature" | "margin_annotation" | "stamp";
export type OCRStatus = "ok" | "ocr_partial" | "failed";
export type DocumentStatus =
  | "uploaded"
  | "preprocessing"
  | "ocr_running"
  | "ocr_done"
  | "ocr_partial"
  | "extracting"
  | "indexed"
  | "needs_review"
  | "ready"
  | "enqueue_failed"
  | "error";
export type FlagType = "low_ocr_confidence" | "illegible" | "entity_conflict" | "extraction_failure";
export type FlagSeverity = "low" | "medium" | "high";
export type ReviewFlagStatus = "open" | "resolved" | "dismissed";

export interface SessionOut {
  reviewer: string;
}

export interface DocumentCreated {
  id: string;
  filename: string;
  status: string;
  enqueued?: boolean;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  upload_time: string;
  status: DocumentStatus;
}

export interface DocumentListResponse {
  results: DocumentSummary[];
  limit: number;
  offset: number;
  total: number;
}

export interface OCRResultOut {
  engine: string;
  text: string;
  /** 0-100 */
  confidence: number;
  status: OCRStatus;
}

export interface EntityOut {
  id: string;
  entity_type: EntityType;
  raw_text: string;
  normalized_value: string | null;
  /** 0-1 */
  confidence: number;
  corrected: boolean;
}

export interface RegionOut {
  id: string;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  region_type: RegionType;
  reading_order: number;
  confidence: number;
  ocr_result: OCRResultOut | null;
  entities: EntityOut[];
}

export interface PageOut {
  id: string;
  page_number: number;
  regions: RegionOut[];
}

export interface ReviewFlagOut {
  id: string;
  flag_type: FlagType;
  severity: FlagSeverity;
  explanation: string;
  status: ReviewFlagStatus;
  page_id: string | null;
  region_id: string | null;
  entity_id: string | null;
  created_at: string;
  status_changed_at: string | null;
}

export interface ReviewFlagWithDocument extends ReviewFlagOut {
  document_id: string;
  document_filename: string;
}

export interface ReviewFlagListResponse {
  results: ReviewFlagWithDocument[];
  limit: number;
  offset: number;
  total: number;
}

export interface DocumentDetail {
  id: string;
  filename: string;
  upload_time: string;
  status: DocumentStatus;
  error_message?: string | null;
  pages: PageOut[];
  flags: ReviewFlagOut[];
}

export interface SearchResultItem {
  page_id: string;
  document_id: string;
  filename: string;
  page_number: number;
  rank: number;
  /** ts_headline output — matched terms wrapped in <b>...</b>. See SnippetHighlight for safe rendering. */
  snippet: string;
}

export interface SearchResponse {
  results: SearchResultItem[];
  limit: number;
  offset: number;
  total: number;
}

export interface SearchFilters {
  q: string;
  date_from?: string;
  date_to?: string;
  entity_type?: EntityType;
  location?: string;
  min_confidence?: number;
  limit?: number;
  offset?: number;
}

export interface EntityCorrectionRequest {
  corrected_value: string;
}

export interface EntityCorrectionOut {
  entity_id: string;
  original_value: string | null;
  corrected_value: string;
  reviewer: string;
  corrected_at: string;
}

export interface ReviewFlagUpdateRequest {
  status: "resolved" | "dismissed";
}

export interface StatsResponse {
  total_documents: number;
  documents_indexed: number;
  documents_needing_review: number;
  open_review_flags: number;
  /** 0-100 */
  average_ocr_confidence: number | null;
  /** 0-1 */
  average_entity_confidence: number | null;
  open_flags_by_type: Record<string, number>;
}
