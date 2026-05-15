"use client";

import { useState, useEffect, useCallback, useLayoutEffect } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Reader from "@/components/Reader";
import { contentAPI } from "@/lib/api";
import { ContentItem } from "@/types";
import Link from "next/link";

const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

export default function ContentPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const contentId = params.id as string;
  const initialHighlightId = searchParams.get("h") ?? undefined;

  const getCachedContent = () => {
    if (typeof window === "undefined") return null;
    try {
      const cached = sessionStorage.getItem(`contentItemCache_${contentId}`);
      if (cached) {
        const parsed = JSON.parse(cached);
        // Only use cache if it has full_text — list-shape items don't and would show blank reader
        if (
          parsed &&
          parsed.item &&
          parsed.item.full_text &&
          Date.now() - parsed.timestamp < 3600000
        ) {
          return parsed.item;
        }
      }
    } catch {}
    return null;
  };

  const [content, setContent] = useState<ContentItem | null>(() =>
    getCachedContent(),
  );
  const [loading, setLoading] = useState<boolean>(() => {
    const cached = getCachedContent();
    return cached ? false : true;
  });
  const [error, setError] = useState<string | null>(null);

  /**
   * Synchronously load cache before the browser paints to prevent
   * a 1-frame flash of the RetroLoader.
   */
  useIsomorphicLayoutEffect(() => {
    try {
      const cached = sessionStorage.getItem(`contentItemCache_${contentId}`);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (
          parsed &&
          parsed.item &&
          parsed.item.full_text &&
          Date.now() - parsed.timestamp < 3600000
        ) {
          setContent(parsed.item);
          setLoading(false);
        }
      }
    } catch {}
  }, [contentId]);

  const fetchContent = useCallback(
    async (forceRefresh = false, silent = false) => {
      try {
        if (!forceRefresh) {
          try {
            const cached = sessionStorage.getItem(
              `contentItemCache_${contentId}`,
            );
            if (cached) {
              const parsed = JSON.parse(cached);
              if (
                parsed &&
                parsed.item &&
                parsed.item.full_text &&
                Date.now() - parsed.timestamp < 3600000
              ) {
                setContent(parsed.item);
                if (!silent) setLoading(false);
                return;
              }
            }
          } catch {}
        }

        if (!silent) setLoading(true);
        setError(null);

        const data = await contentAPI.getFullById(contentId);
        setContent(data);

        try {
          sessionStorage.setItem(
            `contentItemCache_${contentId}`,
            JSON.stringify({ item: data, timestamp: Date.now() }),
          );
        } catch {}
      } catch (err) {
        console.error("Failed to fetch content:", err);
        if (!silent)
          setError(
            "Couldn't load article. It may not exist or you may not have access.",
          );
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [contentId],
  );

  useEffect(() => {
    fetchContent();

    const handleFocus = () => {
      fetchContent(true, true);
    };
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [fetchContent]);

  const handleStatusChange = async (updates: {
    is_read?: boolean;
    is_archived?: boolean;
    read_position?: number;
    full_text?: string;
    is_public?: boolean;
  }) => {
    if (!content) return;

    const previousContent = { ...content };

    try {
      // Optimistic update
      setContent({ ...content, ...updates });

      // Persist to backend and get updated content with computed reading_status
      const updatedContent = await contentAPI.update(contentId, updates);

      // PATCH returns ContentItemResponse (no full_text). Preserve full_text from current state
      // so the reader body doesn't disappear after a read-position or status update.
      const mergedContent = {
        ...updatedContent,
        full_text: previousContent.full_text,
      };
      setContent(mergedContent);

      // Update the cached content in sessionStorage so it reflects when navigating back
      try {
        // Update specific item cache
        sessionStorage.setItem(
          `contentItemCache_${contentId}`,
          JSON.stringify({ item: mergedContent, timestamp: Date.now() }),
        );

        // Update list cache (use updatedContent — list shape is correct, no full_text needed there)
        const cachedData = sessionStorage.getItem("contentListCache");
        if (cachedData) {
          const cache = JSON.parse(cachedData);
          if (cache.items && Array.isArray(cache.items)) {
            cache.items = cache.items.map((item: ContentItem) =>
              item.id === contentId ? updatedContent : item,
            );
            sessionStorage.setItem("contentListCache", JSON.stringify(cache));
          }
        }
      } catch (cacheErr) {
        // Silently fail - cache update is not critical
        console.warn("Failed to update cache:", cacheErr);
      }
    } catch (err) {
      console.error("Failed to update content:", err);
      // Revert on error
      setContent(previousContent);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center">
        <div className="text-center font-mono">
          {/* Typewriter dots animation */}
          <div className="flex items-center justify-center gap-1 text-[var(--color-text-muted)]">
            <span className="inline-block animate-pulse">.</span>
            <span className="inline-block animate-pulse [animation-delay:0.3s]">
              .
            </span>
            <span className="inline-block animate-pulse [animation-delay:0.6s]">
              .
            </span>
          </div>
        </div>
      </div>
    );
  }

  if (error || !content) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center">
        <div className="text-center max-w-md px-4">
          <h1 className="font-serif text-2xl font-normal text-[var(--color-text-primary)] mb-2">
            Article Not Found
          </h1>
          <p className="text-[var(--color-text-secondary)] mb-6">
            {error || "This article could not be loaded."}
          </p>
          <Link
            href="/dashboard"
            className="inline-block bg-[var(--color-accent)] text-white px-6 py-2 hover:bg-[var(--color-accent-hover)] transition-colors"
          >
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <Reader
      content={content}
      onStatusChange={handleStatusChange}
      initialHighlightId={initialHighlightId}
    />
  );
}
