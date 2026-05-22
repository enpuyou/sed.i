# ADR-0004: Object Storage Strategy

**Status:** Accepted
**Date:** 2026-05-22

---

## Context

PDFs ingested by sed.i are fetched, processed in memory, and the extracted HTML/text is stored in Postgres. The raw PDF bytes are discarded after extraction. This means:

- PDFs can't be re-processed if the extraction pipeline improves (e.g., better YOLO model)
- There is no way to let users download the original document
- YOLO model weights (~100MB) are downloaded from HuggingFace at runtime and cached in the Railway container's ephemeral filesystem — lost on restarts

S3 solves both problems: durable raw PDF storage and a place for model weights if needed.

---

## Decision

### S3 bucket

A private S3 bucket (`sedi-assets-{env}`) managed by Pulumi stores raw PDFs keyed as `pdfs/{user_id}/{item_id}.pdf`. No public access — all reads go through presigned URLs generated server-side.

### Encryption

SSE-S3 (AES-256) — no KMS. PDFs are user-submitted public web content; they are not sensitive enough to warrant KMS key management costs or complexity.

### Lifecycle

Standard → Standard-IA after 90 days → Glacier after 365 days. PDFs are accessed most in the first days after ingestion (re-processing, reading). After that, access drops sharply. IA and Glacier tiers cut storage cost significantly at the expense of retrieval latency, which is acceptable for old items.

### Upload path

`extraction.py` uploads raw bytes immediately after `_process_pdf()` completes. The `s3_key` is stored on `ContentItem`. Upload failures are logged and swallowed — they don't abort ingestion. The app degrades gracefully: items without `s3_key` simply have no downloadable PDF.

### Read path

`GET /content/{item_id}/pdf-url` generates a presigned URL (default 1 hour TTL, configurable via `AWS_S3_PRESIGN_EXPIRY`). The frontend or client fetches the PDF directly from S3 — the backend is not in the data path.

### YOLO model weights

Model weights are currently downloaded from HuggingFace and cached in the container filesystem. The plan considered storing them on S3 or Modal, but:
- HuggingFace caching already handles this at no cost
- Model weights are read-only and Railway restarts are infrequent
- Modal adds infra complexity without a clear win at current ingestion volume

Decision: leave YOLO weights on HuggingFace cache for now. Revisit when running on a stateless container platform where the cache layer cannot persist (e.g., Lambda).

---

## Alternatives considered

### Store PDFs in Postgres (as bytea)
- **Pros:** Zero new infra, already have a DB connection.
- **Rejected because:** Postgres is not designed for binary blobs. PDFs can be 50MB+. This would bloat the DB, slow VACUUM, and saturate Railway's disk quota quickly.

### Railway persistent volumes
- **Pros:** Zero AWS, simple path.
- **Cons:** Not replicated, Railway volumes are per-region, no lifecycle tiering, no presigned URLs.
- **Rejected because:** S3 is the standard for object storage, teaches the right patterns, and is cheaper per GB.

### Cloudflare R2
- **Pros:** No egress cost, S3-compatible API, cheap.
- **Cons:** Less AWS learning value, Pulumi provider is less mature than `pulumi-aws`.
- **Rejected because:** The learning goal is AWS specifically. R2 is a valid choice if cost becomes a concern.

---

## Consequences

- `boto3` is already a dependency (added in Layer 4). No new deps needed.
- `AWS_S3_BUCKET` left empty → all S3 operations are silent no-ops. Dev and test environments work without AWS credentials.
- Existing items ingested before Layer 6 have no `s3_key` — they will return 404 on the pdf-url endpoint. Re-ingestion is required to backfill.
- PDF reader in the frontend can be extended to display the original PDF inline via the presigned URL.

---

## Migration trigger

No migration needed for switching providers — the key layout (`pdfs/{user_id}/{item_id}.pdf`) is bucket-agnostic. If moving from S3 to R2, copy objects with `aws s3 sync`, update `AWS_S3_BUCKET`, redeploy.
