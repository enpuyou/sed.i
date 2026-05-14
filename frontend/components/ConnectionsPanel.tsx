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
  activeHighlightId: string | null; // null = Mode 2; set = Mode 1
  isOpen: boolean;
  onBackToAll: () => void;
  onSelectHighlight: (highlightId: string) => void; // Mode 2 card click → Mode 1
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

  const handleNavigate = (articleId: string) => {
    onNavigateToArticle?.(articleId);
    router.push(`/content/${articleId}`);
  };

  if (!isOpen) return null;

  if (activeHighlightId) {
    return (
      <Mode1Panel
        highlightId={activeHighlightId}
        onBackToAll={onBackToAll}
        onNavigate={handleNavigate}
      />
    );
  }

  return (
    <Mode2Panel
      contentId={contentId}
      onSelectHighlight={onSelectHighlight}
      onNavigate={handleNavigate}
    />
  );
}

// ── Mode 1: single highlight ──────────────────────────────────────────────────

function Mode1Panel({
  highlightId,
  onBackToAll,
  onNavigate,
}: {
  highlightId: string;
  onBackToAll: () => void;
  onNavigate: (articleId: string) => void;
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

  return (
    <div className="h-full flex flex-col bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
      {/* Compact back button — top-left, matches HighlightsPanel Copy button */}
      <div className="px-3 pt-2.5 pb-1.5">
        <button
          onClick={onBackToAll}
          className="font-mono text-[10px] px-2 py-0.5 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-faint)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
        >
          ← all highlights
        </button>
      </div>

      {/* Content */}
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
            {/* Source highlight note */}
            {data.source_note && (
              <div className="border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
                <p className="font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1">
                  Your note
                </p>
                <p className="font-serif text-[13px] text-[var(--color-text-secondary)] leading-relaxed">
                  {data.source_note}
                </p>
              </div>
            )}

            {/* Connection cards */}
            {data.connections.map((conn) => (
              <Mode1Card
                key={conn.article_id}
                connection={conn}
                highlightId={highlightId}
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
  connection,
  highlightId,
  onNavigate,
}: {
  connection: HighlightArticleConnection;
  highlightId: string;
  onNavigate: (articleId: string) => void;
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
      className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] hover:border-[var(--color-accent)] transition-colors"
      onClick={() => onNavigate(connection.article_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onNavigate(connection.article_id)}
    >
      {/* Zone A: article identity */}
      <div className="px-3 pt-3 pb-3">
        <p className="font-serif text-[13px] text-[var(--color-text-primary)] leading-snug mb-0.5">
          {connection.article_title}
        </p>
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

      {/* Zone B: matched passages */}
      <div className="border-t border-[var(--color-border)] px-3 pt-2.5 pb-3">
        {connection.passages.map((passage, i) => (
          <p
            key={i}
            className={`font-serif text-[13px] text-[var(--color-text-secondary)] leading-relaxed ${
              i > 0
                ? "border-t border-[var(--color-border-subtle)] mt-2.5 pt-2.5"
                : ""
            }`}
          >
            {passage}
          </p>
        ))}
      </div>
    </div>
  );
}

// ── Mode 2: all highlights ────────────────────────────────────────────────────

function Mode2Panel({
  contentId,
  onSelectHighlight,
  onNavigate,
}: {
  contentId: string;
  onSelectHighlight: (highlightId: string) => void;
  onNavigate: (articleId: string) => void;
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
    <div className="h-full flex flex-col bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
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
              onNavigate={onNavigate}
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
  onNavigate,
}: {
  item: HighlightWithConnections;
  onSelectHighlight: (highlightId: string) => void;
  onNavigate: (articleId: string) => void;
}) {
  return (
    <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)]">
      {/* Highlight header — click to enter Mode 1 */}
      <div
        className="px-3 py-3 bg-[var(--color-bg-tertiary)] border-b border-[var(--color-border)] cursor-pointer hover:bg-[var(--color-bg-secondary)] transition-colors"
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

      {/* Connected articles */}
      {item.connections.map((conn, i) => (
        <div
          key={conn.article_id}
          className={`px-3 py-3 cursor-pointer hover:bg-[var(--color-bg-secondary)] transition-colors ${
            i > 0 ? "border-t border-[var(--color-border)]" : ""
          }`}
          onClick={() => onNavigate(conn.article_id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && onNavigate(conn.article_id)}
        >
          <p className="font-serif text-[13px] text-[var(--color-text-primary)] mb-0.5">
            {conn.article_title}
          </p>
          {(conn.article_author || conn.article_domain) && (
            <p className="font-mono text-[10px] text-[var(--color-text-faint)] mb-2">
              {conn.article_author && (
                <span className="text-[var(--color-text-muted)]">
                  {conn.article_author}
                </span>
              )}
              {conn.article_author && conn.article_domain && " · "}
              {conn.article_domain}
            </p>
          )}
          {conn.shared_tags.length > 0 && (
            <p className="font-mono text-[10px] text-[var(--color-text-muted)] mb-2">
              {conn.shared_tags.map((t) => `● ${t}`).join("  ")}
            </p>
          )}
          {/* Passages separated from meta */}
          <div className="border-t border-[var(--color-border-subtle)] pt-2.5">
            {conn.passages.map((passage, pi) => (
              <p
                key={pi}
                className={`font-serif text-[13px] text-[var(--color-text-secondary)] leading-relaxed ${
                  pi > 0
                    ? "border-t border-[var(--color-border-subtle)] mt-2.5 pt-2.5"
                    : ""
                }`}
              >
                {passage}
              </p>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
