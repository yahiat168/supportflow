# SupportFlow knowledge base

These files form the internal knowledge base for the SupportFlow project. Each file starts with metadata that should be preserved during ingestion.

Required ingestion behaviour:

- Read the metadata fields.
- Store `doc_id`, `product`, `version`, `source_type`, and `trust_level` as vector metadata.
- Split the body into useful chunks without losing the source document ID.
- Return document IDs and source titles with every retrieval result.
- Support filtering by product, version, and source type.
- Prefer official and newer documents when sources conflict.

The corpus is intentionally small enough to run locally and rich enough to test citations, filtering, conflicts, tool routing, and escalation.
