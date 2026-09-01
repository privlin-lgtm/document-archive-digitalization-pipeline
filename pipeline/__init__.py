"""Orchestrates the existing per-stage modules (ocr.preprocess, ocr.layout,
ocr.engine, extraction.entities, extraction.anomalies) into one pipeline that
runs on document upload — see pipeline.run.run_pipeline and worker.run_ocr_job.
"""
