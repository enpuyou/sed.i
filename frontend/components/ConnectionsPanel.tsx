"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { searchAPI } from "@/lib/api";
import RetroLoader from "./RetroLoader";
import InlineError from "./InlineError";
import EmptyState from "./EmptyState";

interface HighlightPair {
  user_highlight_id: string;
  user_highlight_text: string;
  connected_highlight_id: string;
  connected_highlight_text: string;
  similarity: number;
}

interface ArticleConnection {
  article_id: string;
  article_title: string;
  highlight_pairs: HighlightPair[];
  total_similarity: number;
  shared_tags: string[];
}

interface ConnectionsPanelProps {
  contentId: string;
  isOpen: boolean;
  onClose: () => void;
  onNavigateToArticle?: (contentId: string) => void;
}

export default function ConnectionsPanel({
  contentId,
  isOpen,
  onClose: _onClose,
  onNavigateToArticle,
}: ConnectionsPanelProps) {
  const [connections, setConnections] = useState<ArticleConnection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasFetched, setHasFetched] = useState(false);
  const [fetchedForId, setFetchedForId] = useState<string | null>(null);
  const router = useRouter();

  const fetchConnections = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await searchAPI.findArticleConnections(contentId);
      setConnections(data);
      setHasFetched(true);
      setFetchedForId(contentId);
    } catch (err) {
      console.error("Failed to load connections:", err);
      setError(
        err instanceof Error ? err.message : "Couldn't load connections.",
      );
    } finally {
      setLoading(false);
    }
  }, [contentId]);

  useEffect(() => {
    if (fetchedForId && fetchedForId !== contentId) {
      setConnections([]);
      setHasFetched(false);
      setError(null);
      setFetchedForId(null);
    }
  }, [contentId, fetchedForId]);

  useEffect(() => {
    if (!isOpen || hasFetched) return;
    fetchConnections();
  }, [isOpen, hasFetched, fetchConnections]);

  const handleNavigateToArticle = (articleId: string, highlightId?: string) => {
    const url = `/content/${articleId}${highlightId ? `#${highlightId}` : ""}`;
    onNavigateToArticle?.(articleId);
    router.push(url);
  };

  const renderContent = () => {
    if (loading) {
      return (
        <div className="flex items-center justify-center py-12">
          <RetroLoader
            text="Fetching highlight connections"
            className="text-[var(--color-text-muted)]"
          />
        </div>
      );
    }

    if (error) {
      return (
        <div className="p-4">
          <InlineError message={error} onRetry={fetchConnections} />
        </div>
      );
    }

    if (connections.length === 0) {
      return (
        <EmptyState
          message="No connections yet."
          description="Highlight similar concepts across articles to discover connections."
          className="px-4"
        />
      );
    }

    return (
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-8">
        {connections.map((articleConnection) => (
          <ArticleConnectionCard
            key={articleConnection.article_id}
            connection={articleConnection}
            onNavigate={handleNavigateToArticle}
            sourceContentId={contentId}
          />
        ))}
      </div>
    );
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex flex-col h-full">{renderContent()}</div>
    </div>
  );
}

function ArticleConnectionCard({
  connection,
  onNavigate,
  sourceContentId,
}: {
  connection: ArticleConnection;
  onNavigate: (articleId: string, highlightId?: string) => void;
  sourceContentId: string;
}) {
  const pair = connection.highlight_pairs[0];

  return (
    <div className="border border-[var(--color-border)]">
      {/* Article title + shared tags */}
      <div className="px-3 pt-3 pb-2">
        <button
          onClick={() => onNavigate(connection.article_id)}
          className="text-left hover:text-[var(--color-accent)] transition-colors"
        >
          <h4 className="text-sm font-serif font-medium text-[var(--color-text-primary)] line-clamp-2">
            {connection.article_title}
          </h4>
        </button>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5">
          {connection.shared_tags.map((tag) => (
            <span
              key={tag}
              className="text-xs text-[var(--color-text-muted)] cursor-default"
              onClick={() =>
                searchAPI.postTelemetry({
                  surface: "connections_panel",
                  item_id: sourceContentId,
                  shared_tag: tag,
                  action: "click",
                })
              }
            >
              ● {tag}
            </span>
          ))}
        </div>
      </div>

      {/* Highlight comparison — your highlight on top, theirs below */}
      <div className="border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="px-3 py-2 border-b border-[var(--color-border-subtle,var(--color-border))]">
          <p className="text-xs text-[var(--color-text-primary)] line-clamp-3">
            {pair.user_highlight_text}
          </p>
        </div>
        <button
          onClick={() =>
            onNavigate(connection.article_id, pair.connected_highlight_id)
          }
          className="w-full text-left px-3 py-2 hover:bg-[var(--color-bg-tertiary)] transition-colors group"
        >
          <p className="text-xs text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)] line-clamp-3 transition-colors">
            {pair.connected_highlight_text}
          </p>
          <p className="text-[10px] text-[var(--color-text-faint)] mt-1">
            {(pair.similarity * 100).toFixed(0)}% · open article →
          </p>
        </button>
      </div>
    </div>
  );
}
