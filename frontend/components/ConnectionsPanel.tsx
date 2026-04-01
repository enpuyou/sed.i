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

  // Reset state when article changes
  useEffect(() => {
    if (fetchedForId && fetchedForId !== contentId) {
      setConnections([]);
      setHasFetched(false);
      setError(null);
      setFetchedForId(null);
    }
  }, [contentId, fetchedForId]);

  // Fetch when panel opens (or article changed while open)
  useEffect(() => {
    if (!isOpen || hasFetched) return;
    fetchConnections();
  }, [isOpen, hasFetched, fetchConnections]);

  const handleNavigateToArticle = (articleId: string, highlightId?: string) => {
    const url = `/content/${articleId}${highlightId ? `#${highlightId}` : ""}`;
    onNavigateToArticle?.(articleId);
    router.push(url);
  };

  // Exclusive state rendering: loading > error > empty > data
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
          <div
            key={articleConnection.article_id}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-none shadow-lg"
          >
            {/* Article Title - clickable */}
            <button
              onClick={() =>
                handleNavigateToArticle(articleConnection.article_id)
              }
              className="w-full text-left p-3 hover:bg-[var(--color-bg-tertiary)] transition-colors"
            >
              <h4 className="text-sm font-serif font-medium text-[var(--color-text-primary)] line-clamp-2">
                {articleConnection.article_title}
              </h4>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">
                {articleConnection.highlight_pairs.length} highlight
                {articleConnection.highlight_pairs.length !== 1 ? "s" : ""}
              </p>
            </button>

            {/* Highlight Pairs */}
            <div className="bg-[var(--color-bg-tertiary)] border-t border-[var(--color-border)] px-3 py-2 space-y-2">
              {articleConnection.highlight_pairs
                .slice(0, 3)
                .map((pair, idx) => (
                  <div key={idx} className="text-xs">
                    <p className="text-[var(--color-text-muted)] mb-1">
                      {(pair.similarity * 100).toFixed(0)}% match
                    </p>
                    <div className="space-y-1">
                      <p className="block text-left text-[var(--color-text-primary)] text-xs line-clamp-2">
                        <span className="text-[var(--color-text-muted)]">
                          Your:{" "}
                        </span>
                        {pair.user_highlight_text}
                      </p>
                      <button
                        onClick={() =>
                          handleNavigateToArticle(
                            articleConnection.article_id,
                            pair.connected_highlight_id,
                          )
                        }
                        className="block text-left text-[var(--color-text-primary)] hover:text-[var(--color-accent)] transition-colors text-xs line-clamp-2"
                      >
                        <span className="text-[var(--color-text-muted)]">
                          Their:{" "}
                        </span>
                        {pair.connected_highlight_text}
                      </button>
                    </div>
                  </div>
                ))}
              {articleConnection.highlight_pairs.length > 3 && (
                <p className="text-xs text-[var(--color-text-muted)] pt-1">
                  +{articleConnection.highlight_pairs.length - 3} more
                  connections
                </p>
              )}
            </div>
          </div>
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
