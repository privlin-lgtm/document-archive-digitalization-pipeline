"""Lightweight evaluation harness: measures OCR (CER/WER) and entity
extraction (precision/recall/F1 per type) quality against a small
hand-labeled ground-truth set, decoupled from the DB/Celery pipeline so it's
runnable as a standalone script. See eval/README.md.
"""
