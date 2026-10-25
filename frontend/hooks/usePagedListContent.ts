"use client";

import { SetStateAction, useCallback, useRef, useState } from "react";
import { listsAPI } from "@/lib/api";
import { ContentItem } from "@/types";

export const LIST_PAGE_SIZE = 50;

// Items of one list, loaded a page at a time. The first page is supplied by
// the caller via reset(); loadMore() appends the next one. The caller still
// owns edits (it gets setContents) and adjusts the total after local removals.
export function usePagedListContent(
  listId: string,
  pageSize: number = LIST_PAGE_SIZE,
) {
  const [contents, setContents] = useState<ContentItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const loadedRef = useRef(0);
  const inFlightRef = useRef(false);
  const generationRef = useRef(0);

  const setLoaded = useCallback((update: SetStateAction<ContentItem[]>) => {
    setContents((prev) => {
      const next = typeof update === "function" ? update(prev) : update;
      loadedRef.current = next.length;
      return next;
    });
  }, []);

  // Replace everything with a freshly fetched first page.
  const reset = useCallback(
    (firstPage: ContentItem[], totalCount: number) => {
      generationRef.current += 1;
      inFlightRef.current = false;
      loadedRef.current = firstPage.length;
      setContents(firstPage);
      setTotal(totalCount);
      setExhausted(firstPage.length < pageSize);
      setLoadingMore(false);
      setLoadMoreError(null);
    },
    [pageSize],
  );

  const loadMore = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    const generation = generationRef.current;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      // Skip by what is loaded now: local removals already shifted the
      // server's positions the same way.
      const next: ContentItem[] = await listsAPI.getContent(listId, {
        skip: loadedRef.current,
        limit: pageSize,
      });
      if (generation !== generationRef.current) return;
      // Items added elsewhere meanwhile can shift a row into two pages.
      setLoaded((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        return [...prev, ...next.filter((c) => !seen.has(c.id))];
      });
      if (next.length < pageSize) setExhausted(true);
    } catch (err) {
      if (generation !== generationRef.current) return;
      console.error("Failed to load more list items:", err);
      setLoadMoreError("Couldn't load more items. Try again.");
    } finally {
      if (generation === generationRef.current) {
        inFlightRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [listId, pageSize, setLoaded]);

  const adjustTotal = useCallback((delta: number) => {
    setTotal((t) => (t === null ? t : Math.max(0, t + delta)));
  }, []);

  const hasMore = total !== null && !exhausted && contents.length < total;
  // Once a short page proves the end, trust what is loaded over the count.
  const displayTotal = total === null || exhausted ? contents.length : total;

  return {
    contents,
    setContents: setLoaded,
    reset,
    loadMore,
    adjustTotal,
    hasMore,
    displayTotal,
    loadingMore,
    loadMoreError,
  };
}
