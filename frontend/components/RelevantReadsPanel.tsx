"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { draftsAPI, RelevantReadItem } from "@/lib/api";

interface RelevantReadsPanelProps {
  listId: string;
  savedAt: number | null;
}

export default function RelevantReadsPanel({
  listId,
  savedAt,
}: RelevantReadsPanelProps) {
  const [items, setItems] = useState<RelevantReadItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasFetched, setHasFetched] = useState(false);
  const lastFetchedAt = useRef<number | null>(null);

  useEffect(() => {
    if (savedAt === null || savedAt === lastFetchedAt.current) return;
    lastFetchedAt.current = savedAt;
    setLoading(true);
    draftsAPI
      .getRelevantReads(listId)
      .then((data) => setItems(data.items))
      .catch(() => setItems([]))
      .finally(() => {
        setLoading(false);
        setHasFetched(true);
      });
  }, [listId, savedAt]);

  // Don't mount until first save has happened
  if (savedAt === null) return null;

  return (
    <div className="border-t border-[var(--color-border)] px-4 py-3">
      <p className="text-xs font-mono uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
        Relevant reading
      </p>
      {loading && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Finding articles…
        </p>
      )}
      {!loading && hasFetched && items.length === 0 && (
        <p className="text-xs text-[var(--color-text-faint)] italic">
          No matches yet. Write more to surface related articles.
        </p>
      )}
      {!loading && items.length > 0 && (
        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.id}>
              <Link
                href={`/content/${item.id}`}
                className="block text-xs text-[var(--color-text-primary)] hover:text-[var(--color-accent)] transition-colors line-clamp-1"
              >
                {item.title ?? "Untitled"}
              </Link>
              {item.tags.length > 0 && (
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
                  {item.tags.slice(0, 3).map((tag) => (
                    <span
                      key={tag}
                      className="text-[10px] text-[var(--color-text-muted)]"
                    >
                      ● {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
