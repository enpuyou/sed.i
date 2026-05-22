# ADR-0002: Observability Stack — OTEL + Braintrust + Sentry + Grafana

**Status:** Accepted
**Date:** 2026-05-22

---

## Context

sed.i makes LLM calls (embeddings, tagging, summarization, MCP synthesis) and serves HTTP traffic. Currently there is no tracing, no LLM call attribution, and no error tracking. We need:

1. **LLM observability** — cost, latency, input/output per call, per task type
2. **Infra tracing** — HTTP request traces, Celery task traces, DB query traces
3. **Error tracking** — frontend and backend exceptions with context
4. **Dashboards** — request rate, p99 latency, error rate, token spend

Options evaluated per concern:

**LLM observability:**
- Braintrust — eval-centric, used at Notion/Vercel/Stripe, generous free tier, `wrap_openai` integration
- Langfuse — OSS, self-hostable, more general tracing focus
- Arize Phoenix — OSS, eval + tracing, heavier setup
- Helicone — proxy-based, lightest setup, less eval capability
- LangSmith — LangChain-coupled, not relevant for our stack

**Infra tracing:**
- OpenTelemetry SDK → Grafana Cloud — OTEL is the industry standard; swap backends without code changes
- Datadog — expensive at any real scale, overkill for personal project
- Honeycomb — excellent DX for traces, slightly more DX-focused than Grafana
- New Relic — free tier generous but vendor-lock-in concern

**Error tracking:**
- Sentry — industry default, free tier covers personal usage (5K errors, 10K perf events)
- Rollbar — similar but less widely known
- Highlight.io — OSS alternative, session replay included

---

## Decision

**LLM observability:** Braintrust
**Infra tracing:** OpenTelemetry SDK → Grafana Cloud free tier
**Error tracking:** Sentry (backend + frontend)

---

## Rationale

**Braintrust** is the right LLM observability choice because sed.i's primary need is *eval-driven development* — the goal is to measure retrieval quality, tagging quality, and synthesis quality, not just log calls. Braintrust's dataset + experiment + scorer workflow maps directly to this. `wrap_openai` (and future `wrap_bedrock`) means instrumentation is a one-liner in `LLMClient`. Langfuse is a close alternative and would be preferred if self-hosting is a hard requirement; for now free-tier SaaS is fine.

**OpenTelemetry** is chosen over vendor-specific SDKs because: (1) it is the actual industry standard — instrument once, swap backends; (2) `opentelemetry-instrumentation-fastapi` gives automatic span generation with zero app-logic changes; (3) Grafana Cloud's free tier (10K series, 50GB logs, 50GB traces) covers personal scale with headroom.

Honeycomb was a close second for traces — its DX for distributed tracing is superior to Grafana. Choosing Grafana because: OTEL export to Grafana is the more common production pattern, PromQL/LogQL skills transfer directly to any Grafana-based infra, and the free tier is more generous for a project at this scale.

**Sentry** is the default error tracker at most companies. Zero setup for FastAPI (`sentry-sdk[fastapi]`), source maps for Next.js. No reason to deviate from the default.

---

## Tradeoffs accepted

- **Braintrust SaaS** — data leaves the system. Acceptable for a personal reading app with no PII beyond article content.
- **Grafana Cloud free tier limits** — 10K metric series. sed.i will stay well under this for the foreseeable future. If we hit limits, migrate to self-hosted Grafana stack (compose file) or Honeycomb.
- **Three separate tools** — Braintrust for LLM, Grafana for infra, Sentry for errors. This is intentional: each tool is best-in-class for its concern. The alternative (one tool like Datadog) costs money and creates vendor lock-in.

---

## Implementation order

1. Braintrust: wrap `LLMClient` calls — highest signal, no infra required
2. Sentry: add SDK to FastAPI + Next.js — low effort, immediate value
3. OTEL: instrument FastAPI, export to console first, then wire Grafana Cloud
4. Grafana: build first dashboard once traces are flowing

---

## What would change this decision

- **Langfuse over Braintrust** if we need full data sovereignty (self-host requirement)
- **Honeycomb over Grafana** if trace DX becomes a bottleneck (Honeycomb's query interface is better for debugging distributed traces)
- **Datadog** only if this becomes a multi-engineer project with a budget

---

## References

- Braintrust docs: https://www.braintrust.dev/docs
- OpenTelemetry Python: https://opentelemetry-python.readthedocs.io
- sed.i LLM entry point: `app/core/llm_client.py`
