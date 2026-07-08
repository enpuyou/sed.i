# Entity Graph Hardening + E2E Test
Date: 2026-07-06

## What changed

Fixes, tests, and documentation for the entity graph system introduced on `enhancement/sota-stack`.

### Bugs fixed

- **Schema drift**: `EntityRelation.__table_args__` was missing the unique constraint that exists in the migration. `Base.metadata.create_all()` in tests built the schema from the model, not migrations, so the constraint was absent — causing `IntegrityError` on entity upserts in tests. Fixed by adding `UniqueConstraint` to the SQLAlchemy model.
- **Upsert race**: `upsert_entity` did a SELECT then INSERT with no conflict guard. Under concurrent workers, two processes could both see no existing entity and both attempt to insert, with one failing. Fixed by catching `IntegrityError` on flush, rolling back, and re-querying.
- **Prompt/field mismatch**: `entity_extraction.py` prompt described `relation_type` but `RelationItem` Pydantic schema used `predicate`. Fixed prompt; removed dead fallback in processing code.
- **Test helper gaps**: `SimpleNamespace` helpers in three test files were missing `mention_context`, `predicate`, and `strength` fields accessed by production code. Fixed with defaults.

### Tests added

- `TestEntityPipelineE2E.test_analyze_embed_search_roundtrip` — full round-trip: `analyze_article` writes entities and mentions → `embed_new_entities` writes embeddings → `_entity_search` returns the article. LLM calls mocked; all DB writes are real.

### Documentation

- `ARCHITECTURE.md`: §11b entity graph (tables, extraction, embedding, dedup, dead fields), entity lane paragraph in §12 hybrid search, eval harness table in testing section
- `docs/design/product/knowledge-connections.md`: §5 Entity Graph Search (what it does, how entities are built, search integration, test steps, dev trigger, implementation notes)

### Dead code annotated

- `entity_extraction.py`: entire module is superseded by `article_analysis.py` (combined tags+entities in one LLM call). Comment added to `celery_app.py` documenting the exclusion. Module left in place pending explicit deletion decision.
- `entities.article_count`: column always 0, never written. `_entity_search` computes live counts via `COUNT(entity_mentions.content_item_id)`. Left in place pending migration to drop it.

## Backward compatibility

All migrations on this branch are additive (new tables, nullable columns). Deploy order: run migrations first, then deploy code. The full-table `search_vector` backfill in migration `e3f7a2c` is safe but slow on large tables — run during low-traffic window.
