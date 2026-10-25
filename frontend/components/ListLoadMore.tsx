"use client";

import InlineError from "@/components/InlineError";

interface ListLoadMoreProps {
  loaded: number;
  total: number;
  loading: boolean;
  error: string | null;
  // Ref for the IntersectionObserver sentinel (see useInfiniteSentinel).
  sentinelRef: (el: HTMLDivElement | null) => void;
  // False where IntersectionObserver is missing: show a button instead.
  observerSupported: boolean;
  onLoadMore: () => void;
}

export default function ListLoadMore({
  loaded,
  total,
  loading,
  error,
  sentinelRef,
  observerSupported,
  onLoadMore,
}: ListLoadMoreProps) {
  return (
    <div ref={sentinelRef} className="flex flex-col items-center gap-3 py-6">
      {loading && (
        <div className="w-full" aria-hidden="true">
          <div className="h-3 w-4/5 bg-[var(--color-bg-secondary)] my-3" />
          <div className="h-3 w-3/5 bg-[var(--color-bg-secondary)] my-3" />
        </div>
      )}
      {error ? (
        <InlineError message={error} onRetry={onLoadMore} className="w-full" />
      ) : (
        <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-muted)]">
          {loading ? "Loading more · " : ""}
          {loaded} of {total}
        </span>
      )}
      {!observerSupported && !loading && !error && (
        <button
          type="button"
          onClick={onLoadMore}
          className="font-mono text-xs px-3 py-1 rounded-none border border-[var(--color-border)] bg-transparent text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors"
        >
          Load more
        </button>
      )}
    </div>
  );
}
