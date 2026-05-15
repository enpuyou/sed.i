"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  searchAPI,
  type ConnectionsForHighlightResponse,
  type HighlightArticleConnection,
  type HighlightWithConnections,
} from "@/lib/api";
import RetroLoader from "./RetroLoader";
import InlineError from "./InlineError";
import EmptyState from "./EmptyState";

// ── Props ─────────────────────────────────────────────────────────────────────

interface ConnectionsPanelProps {
  contentId: string;
  activeHighlightId: string | null;
  isOpen: boolean;
  onBackToAll: () => void;
  onSelectHighlight: (highlightId: string) => void;
  onNavigateToArticle?: (contentId: string) => void;
}

// ── Root component ────────────────────────────────────────────────────────────

export default function ConnectionsPanel({
  contentId,
  activeHighlightId,
  isOpen,
  onBackToAll,
  onSelectHighlight,
  onNavigateToArticle,
}: ConnectionsPanelProps) {
  const router = useRouter();
  // Tracks which article card to scroll to when entering Mode 1 from Mode 2
  const [targetArticleId, setTargetArticleId] = useState<string | null>(null);

  const handleNavigate = (articleId: string, highlightId?: string) => {
    onNavigateToArticle?.(articleId);
    const url = highlightId
      ? `/content/${articleId}?h=${highlightId}`
      : `/content/${articleId}`;
    router.push(url);
  };

  const handleSelectHighlight = (highlightId: string, articleId?: string) => {
    setTargetArticleId(articleId ?? null);
    onSelectHighlight(highlightId);
  };

  // Clear target when going back to Mode 2
  const handleBackToAll = () => {
    setTargetArticleId(null);
    onBackToAll();
  };

  if (!isOpen) return null;

  if (activeHighlightId) {
    return (
      <Mode1Panel
        highlightId={activeHighlightId}
        targetArticleId={targetArticleId}
        onBackToAll={handleBackToAll}
        onNavigate={handleNavigate}
      />
    );
  }

  return (
    <Mode2Panel
      contentId={contentId}
      onSelectHighlight={handleSelectHighlight}
    />
  );
}

// ── Mode 1: single highlight ──────────────────────────────────────────────────

function Mode1Panel({
  highlightId,
  targetArticleId,
  onBackToAll,
  onNavigate,
}: {
  highlightId: string;
  targetArticleId: string | null;
  onBackToAll: () => void;
  onNavigate: (articleId: string, highlightId?: string) => void;
}) {
  const [data, setData] = useState<ConnectionsForHighlightResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastFetchedId = useRef<string | null>(null);

  const fetchConnections = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await searchAPI.findHighlightConnections(highlightId);
      setData(result);
      lastFetchedId.current = highlightId;
    } catch {
      setError("Couldn't load connections. Try again.");
    } finally {
      setLoading(false);
    }
  }, [highlightId]);

  useEffect(() => {
    if (highlightId !== lastFetchedId.current) {
      setData(null);
    }
    fetchConnections();
  }, [highlightId, fetchConnections]);

  // Scroll to the target article card once data has loaded
  useEffect(() => {
    if (!targetArticleId || !data) return;
    const el = document.querySelector(`[data-article-id="${targetArticleId}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [data, targetArticleId]);

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 pt-2.5 pb-1.5">
        <button
          onClick={onBackToAll}
          className="font-mono text-[10px] px-2 py-0.5 border border-[var(--color-border)] bg-transparent text-[var(--color-text-faint)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
        >
          ← all highlights
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-2.5">
        {loading && (
          <div className="flex items-center justify-center py-12">
            <RetroLoader
              text="Finding connections"
              className="text-[var(--color-text-muted)]"
            />
          </div>
        )}

        {error && !loading && (
          <InlineError message={error} onRetry={fetchConnections} />
        )}

        {!loading && !error && data && data.connections.length === 0 && (
          <EmptyState
            message="No connections found."
            description="Highlight passages on this topic in your other articles."
            variant="inline"
          />
        )}

        {!loading && !error && data && data.connections.length > 0 && (
          <>
            <p className="font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-faint)]">
              {data.connections.length}{" "}
              {data.connections.length === 1 ? "connection" : "connections"}
            </p>
            {data.connections.map((conn) => (
              <Mode1Card
                key={conn.article_id}
                highlightId={highlightId}
                connection={conn}
                onNavigate={onNavigate}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ── Mode 1 card ───────────────────────────────────────────────────────────────

function Mode1Card({
  highlightId,
  connection,
  onNavigate,
}: {
  highlightId: string;
  connection: HighlightArticleConnection;
  onNavigate: (articleId: string, highlightId?: string) => void;
}) {
  const [insight, setInsight] = useState<string | null>(null);
  const [insightLoading, setInsightLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setInsight(null);
    setInsightLoading(true);

    searchAPI
      .getConnectionInsight(highlightId, connection.article_id)
      .then((res) => {
        if (!cancelled) {
          setInsight(res.insight);
          setInsightLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setInsightLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [highlightId, connection.article_id]);

  return (
    <div
      className="border border-[var(--color-border)] bg-[var(--color-bg-primary)]"
      data-article-id={connection.article_id}
    >
      {/* Zone A: article identity — not interactive */}
      <div className="px-3 pt-3 pb-3">
        <div className="flex justify-between items-start gap-2 mb-0.5">
          <p className="font-serif text-[13px] text-[var(--color-text-primary)] leading-snug">
            {connection.article_title}
          </p>
          <span className="font-mono text-[9px] text-[var(--color-text-muted)] whitespace-nowrap flex-shrink-0 mt-0.5">
            {(connection.connection_score ?? 0).toFixed(2)}
          </span>
        </div>

        {(connection.article_author || connection.article_domain) && (
          <p className="font-mono text-[10px] text-[var(--color-text-faint)] mb-2">
            {connection.article_author && (
              <span className="text-[var(--color-text-muted)]">
                {connection.article_author}
              </span>
            )}
            {connection.article_author && connection.article_domain && " · "}
            {connection.article_domain}
          </p>
        )}

        {connection.shared_tags.length > 0 && (
          <div className="flex flex-wrap gap-2.5 mb-2">
            {connection.shared_tags.map((tag) => (
              <span
                key={tag}
                className="font-mono text-[10px] text-[var(--color-text-muted)]"
              >
                ● {tag}
              </span>
            ))}
          </div>
        )}

        {insightLoading && (
          <p className="font-mono text-[10px] text-[var(--color-text-faint)] leading-relaxed">
            generating insight…
          </p>
        )}
        {!insightLoading && insight && (
          <p className="font-mono text-[10px] text-[var(--color-text-muted)] leading-relaxed">
            {insight}
          </p>
        )}
      </div>

      {/* Zone B: matched passages — each individually clickable */}
      <div className="border-t border-[var(--color-border)] bg-[var(--color-bg-tertiary)] px-3 pt-2.5 pb-3">
        <p className="font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-faint)] mb-2">
          matched highlights
        </p>
        {connection.passages.map((passage, i) => (
          <p
            key={i}
            className={`font-sans text-[12px] text-[var(--color-text-secondary)] leading-relaxed cursor-pointer hover:text-[var(--color-accent)] transition-colors ${
              i > 0
                ? "border-t border-[var(--color-border-subtle)] mt-2.5 pt-2.5"
                : ""
            }`}
            onClick={() =>
              onNavigate(
                connection.article_id,
                connection.passage_highlight_ids[i],
              )
            }
          >
            {passage}
          </p>
        ))}
      </div>

      {/* Footer: open article (no specific highlight) */}
      <div
        className="flex justify-end px-3 py-1.5 border-t border-[var(--color-border-subtle)] bg-[var(--color-bg-secondary)] cursor-pointer group"
        onClick={() => onNavigate(connection.article_id)}
      >
        <span className="font-mono text-[9px] text-[var(--color-text-faint)] group-hover:text-[var(--color-accent)] transition-colors">
          open article →
        </span>
      </div>
    </div>
  );
}

// ── Mode 2: all highlights ────────────────────────────────────────────────────

function Mode2Panel({
  contentId,
  onSelectHighlight,
}: {
  contentId: string;
  onSelectHighlight: (highlightId: string, articleId?: string) => void;
}) {
  const [data, setData] = useState<HighlightWithConnections[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConnections = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await searchAPI.findHighlightGroupedConnections(contentId);
      setData(result);
    } catch {
      setError("Couldn't load connections. Try again.");
    } finally {
      setLoading(false);
    }
  }, [contentId]);

  useEffect(() => {
    fetchConnections();
  }, [fetchConnections]);

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2.5">
        {loading && (
          <div className="flex items-center justify-center py-12">
            <RetroLoader
              text="Finding connections"
              className="text-[var(--color-text-muted)]"
            />
          </div>
        )}

        {error && !loading && (
          <InlineError message={error} onRetry={fetchConnections} />
        )}

        {!loading && !error && data && data.length === 0 && (
          <EmptyState
            message="No connections yet."
            description="Highlight passages in this article and others on the same topic."
            variant="inline"
          />
        )}

        {!loading &&
          !error &&
          data &&
          data.length > 0 &&
          data.map((item) => (
            <Mode2Card
              key={item.highlight_id}
              item={item}
              onSelectHighlight={onSelectHighlight}
            />
          ))}
      </div>
    </div>
  );
}

// ── Mode 2 card ───────────────────────────────────────────────────────────────

function Mode2Card({
  item,
  onSelectHighlight,
}: {
  item: HighlightWithConnections;
  onSelectHighlight: (highlightId: string, articleId?: string) => void;
}) {
  return (
    <div className="border border-[var(--color-border)]">
      {/* Highlight text — click to enter Mode 1 */}
      <div
        className="px-3 py-3 border-b border-[var(--color-border-subtle)] cursor-pointer hover:bg-[var(--color-bg-secondary)] transition-colors"
        onClick={() => onSelectHighlight(item.highlight_id)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) =>
          e.key === "Enter" && onSelectHighlight(item.highlight_id)
        }
      >
        <p className="font-serif text-[13px] text-[var(--color-text-secondary)] leading-relaxed">
          {item.highlight_text}
        </p>
      </div>

      {/* Connected articles — compact rows */}
      {item.connections.length === 0 ? (
        <p className="px-3 py-2 font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-faint)]">
          no connections yet
        </p>
      ) : (
        item.connections.map((conn, i) => (
          <div
            key={conn.article_id}
            className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer hover:bg-[var(--color-bg-secondary)] transition-colors ${
              i > 0 ? "border-t border-[var(--color-border-subtle)]" : ""
            }`}
            onClick={() =>
              onSelectHighlight(item.highlight_id, conn.article_id)
            }
            role="button"
            tabIndex={0}
            onKeyDown={(e) =>
              e.key === "Enter" &&
              onSelectHighlight(item.highlight_id, conn.article_id)
            }
          >
            <div className="flex-1 min-w-0">
              <p className="font-serif text-[12px] text-[var(--color-text-primary)] overflow-hidden text-ellipsis whitespace-nowrap">
                {conn.article_title}
              </p>
              <p className="font-mono text-[9px] text-[var(--color-text-faint)] overflow-hidden text-ellipsis whitespace-nowrap mt-0.5">
                {conn.article_author && (
                  <span className="text-[var(--color-text-muted)]">
                    {conn.article_author}
                    {conn.article_domain && " · "}
                  </span>
                )}
                {conn.article_domain}
                {conn.shared_tags.length > 0 &&
                  " · " + conn.shared_tags.map((t) => `● ${t}`).join("  ")}
              </p>
            </div>
            <span className="font-mono text-[9px] text-[var(--color-text-muted)] flex-shrink-0">
              {(conn.connection_score ?? 0).toFixed(2)}
            </span>
            <span className="font-mono text-[10px] text-[var(--color-text-faint)] flex-shrink-0">
              →
            </span>
          </div>
        ))
      )}
    </div>
  );
}
