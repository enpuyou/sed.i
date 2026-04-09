/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { searchAPI } from "@/lib/api";
import RetroLoader from "./RetroLoader";

interface SearchResult {
  item: {
    id: string;
    title: string;
    description: string | null;
    thumbnail_url: string | null;
    reading_time_minutes: number | null;
    author: string | null;
  };
  similarity_score: number;
  semantic_score?: number | null;
  match_type?: "filter" | "keyword" | "semantic" | "hybrid";
}

interface SearchModalProps {
  initialQuery?: string;
  onClose: () => void;
}

const PAGE_SIZE = 10;

export default function SearchModal({
  initialQuery = "",
  onClose,
}: SearchModalProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  const [query, setQuery] = useState(initialQuery);
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [dateOpen, setDateOpen] = useState(false);
  const [customDate, setCustomDate] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);

  const runSearch = useCallback(
    async (q: string, pg: number, af: string, bf: string) => {
      if (q.length < 3) {
        setResults([]);
        return;
      }
      setLoading(true);
      setError(false);
      try {
        const data = await searchAPI.semantic(q, {
          limit: PAGE_SIZE + 1, // fetch one extra to know if there's a next page
          offset: pg * PAGE_SIZE,
          after: af || undefined,
          before: bf || undefined,
          mode: "full",
        });
        setHasMore(data.length > PAGE_SIZE);
        setResults(data.slice(0, PAGE_SIZE));
        setFocusedIndex(0);
      } catch {
        setError(true);
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounced search when query/dates change — reset to page 0
  useEffect(() => {
    setPage(0);
    const id = setTimeout(() => runSearch(query, 0, after, before), 300);
    return () => clearTimeout(id);
  }, [query, after, before, runSearch]);

  // Re-run when page changes
  useEffect(() => {
    if (page > 0) runSearch(query, page, after, before);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  // Close on Escape, keyboard nav on ↑↓ Enter
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (results.length > 0)
          setFocusedIndex((i) => Math.min(i + 1, results.length - 1));
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (results.length > 0) setFocusedIndex((i) => Math.max(i - 1, 0));
      }
      if (e.key === "Enter" && results[focusedIndex]) {
        handleSelect(results[focusedIndex].item.id);
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, focusedIndex]);

  const applyPreset = (days: number | null) => {
    if (days === null) {
      setAfter("");
      setBefore("");
      setCustomDate(false);
      return;
    }
    const d = new Date();
    d.setDate(d.getDate() - days);
    setAfter(d.toISOString().slice(0, 10));
    setBefore("");
    setCustomDate(false);
  };

  const activePreset = (() => {
    if (!after && !before) return "any";
    if (before) return "custom";
    const days = Math.round(
      (Date.now() - new Date(after).getTime()) / 86400000,
    );
    if (days <= 8) return "7d";
    if (days <= 31) return "30d";
    if (days <= 92) return "3mo";
    if (days <= 366) return "1yr";
    return "custom";
  })();

  const handleSelect = (id: string) => {
    onClose();
    router.push(`/content/${id}`);
  };

  const renderMatchBadge = (_result: SearchResult) => null;

  return createPortal(
    /* Single container: backdrop + modal centered inside, portaled to body
       to escape the navbar's stacking context */
    <div
      className="fixed inset-0 z-[9999] bg-black/40 backdrop-blur-sm flex items-start justify-center pt-[12vh] px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-2xl flex flex-col"
        style={{ maxHeight: "70vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input row */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)]">
          <svg
            className="w-4 h-4 text-[var(--color-text-muted)] shrink-0"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your library..."
            className="flex-1 bg-transparent font-mono text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none border-none outline-none"
          />
          {loading && (
            <RetroLoader
              text=""
              className="text-xs text-[var(--color-accent)]"
            />
          )}
          <button
            onClick={onClose}
            className="text-xs font-mono text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors px-1"
          >
            esc
          </button>
        </div>

        {/* Date filter */}
        <div className="border-b border-[var(--color-border-subtle)]">
          <button
            type="button"
            onClick={() => setDateOpen((o) => !o)}
            className="flex items-center gap-2 w-full px-4 py-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-tertiary)] transition-colors text-left"
          >
            <span className="text-[10px] font-mono text-[var(--color-text-muted)] uppercase tracking-wider">
              Date
            </span>
            {activePreset !== "any" && (
              <span className="text-[10px] font-mono text-[var(--color-accent)]">
                {activePreset === "7d" && "last 7 days"}
                {activePreset === "30d" && "last 30 days"}
                {activePreset === "3mo" && "last 3 months"}
                {activePreset === "1yr" && "last year"}
                {activePreset === "custom" &&
                  `${after || "any"} → ${before || "any"}`}
              </span>
            )}
            <span className="text-[10px] font-mono text-[var(--color-text-muted)] ml-auto">
              {dateOpen ? "▲" : "▼"}
            </span>
          </button>
          {dateOpen && (
            <div className="px-4 py-2 bg-[var(--color-bg-secondary)] flex flex-col gap-2">
              {/* Preset chips */}
              <div className="flex items-center gap-2 flex-wrap">
                {(
                  [
                    { label: "any time", value: null },
                    { label: "last 7 days", value: 7 },
                    { label: "last 30 days", value: 30 },
                    { label: "last 3 months", value: 90 },
                    { label: "last year", value: 365 },
                  ] as { label: string; value: number | null }[]
                ).map(({ label, value }) => {
                  const key =
                    value === null
                      ? "any"
                      : value === 7
                        ? "7d"
                        : value === 30
                          ? "30d"
                          : value === 90
                            ? "3mo"
                            : "1yr";
                  const active = activePreset === key && !customDate;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => applyPreset(value)}
                      className={`text-[10px] font-mono px-2 py-0.5 border transition-colors ${
                        active
                          ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                          : "border-[var(--color-border-subtle)] text-[var(--color-text-muted)] hover:border-[var(--color-border)] hover:text-[var(--color-text-primary)]"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
                <button
                  type="button"
                  onClick={() => setCustomDate((c) => !c)}
                  className={`text-[10px] font-mono px-2 py-0.5 border transition-colors ${
                    customDate
                      ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                      : "border-[var(--color-border-subtle)] text-[var(--color-text-muted)] hover:border-[var(--color-border)] hover:text-[var(--color-text-primary)]"
                  }`}
                >
                  custom
                </button>
              </div>
              {/* Custom range — only shown when "custom" chip is active */}
              {customDate && (
                <div className="flex items-center gap-3">
                  <input
                    type="date"
                    value={after}
                    max={before || undefined}
                    onChange={(e) => setAfter(e.target.value)}
                    className="text-xs font-mono bg-transparent text-[var(--color-text-primary)] border border-[var(--color-border)] px-1.5 py-0.5 focus:outline-none focus:border-[var(--color-accent)]"
                  />
                  <span className="text-[10px] font-mono text-[var(--color-text-muted)]">
                    →
                  </span>
                  <input
                    type="date"
                    value={before}
                    min={after || undefined}
                    onChange={(e) => setBefore(e.target.value)}
                    className="text-xs font-mono bg-transparent text-[var(--color-text-primary)] border border-[var(--color-border)] px-1.5 py-0.5 focus:outline-none focus:border-[var(--color-accent)]"
                  />
                  {(after || before) && (
                    <button
                      onClick={() => {
                        setAfter("");
                        setBefore("");
                      }}
                      className="text-[10px] font-mono text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors ml-auto"
                    >
                      clear
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Results */}
        <div ref={resultsRef} className="overflow-y-auto flex-1">
          {error && (
            <div className="px-4 py-6 text-sm text-[var(--color-text-muted)] text-center">
              Search failed. Try again.
            </div>
          )}

          {!error && !loading && query.length >= 3 && results.length === 0 && (
            <div className="px-4 py-8 text-sm text-[var(--color-text-muted)] text-center">
              No results for &ldquo;{query}&rdquo;
            </div>
          )}

          {!error && query.length < 3 && (
            <div className="px-4 py-6 text-xs font-mono text-[var(--color-text-muted)] text-center">
              Type at least 3 characters — or use after:/before: date filters
            </div>
          )}

          {results.map((result, i) => (
            <button
              key={result.item.id}
              type="button"
              onClick={() => handleSelect(result.item.id)}
              onMouseEnter={() => setFocusedIndex(i)}
              className={`w-full text-left px-4 py-3 flex items-start gap-3 border-b border-[var(--color-border-subtle)] last:border-b-0 transition-colors ${
                i === focusedIndex
                  ? "bg-[var(--color-bg-secondary)]"
                  : "hover:bg-[var(--color-bg-secondary)]"
              }`}
            >
              {result.item.thumbnail_url && (
                <img
                  src={result.item.thumbnail_url}
                  alt=""
                  className="w-10 h-10 object-cover shrink-0"
                />
              )}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
                  {result.item.title || "Untitled"}
                </p>
                {result.item.author && (
                  <p className="text-xs text-[var(--color-text-muted)] truncate">
                    {result.item.author}
                  </p>
                )}
                {result.item.description && (
                  <p className="text-xs text-[var(--color-text-secondary)] line-clamp-1 mt-0.5">
                    {result.item.description}
                  </p>
                )}
                <div className="flex items-center gap-2 mt-1">
                  {renderMatchBadge(result)}
                  {result.item.reading_time_minutes && (
                    <span className="text-[10px] text-[var(--color-text-muted)]">
                      {result.item.reading_time_minutes} min read
                    </span>
                  )}
                </div>
              </div>
              {/* Keyboard hint on focused item */}
              {i === focusedIndex && (
                <span className="text-[10px] font-mono text-[var(--color-text-muted)] shrink-0 self-center">
                  ↵
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Pagination footer */}
        {results.length > 0 && (
          <div className="flex items-center justify-between px-4 py-2 border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="text-xs font-mono text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              ← prev
            </button>
            <span className="text-[10px] font-mono text-[var(--color-text-muted)]">
              page {page + 1}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasMore}
              className="text-xs font-mono text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              next →
            </button>
          </div>
        )}

        {/* Footer hint */}
        <div className="flex items-center gap-4 px-4 py-1.5 border-t border-[var(--color-border-subtle)] bg-[var(--color-bg-secondary)]">
          <span className="text-[10px] font-mono text-[var(--color-text-muted)]">
            ↑↓ navigate
          </span>
          <span className="text-[10px] font-mono text-[var(--color-text-muted)]">
            ↵ open
          </span>
          <span className="text-[10px] font-mono text-[var(--color-text-muted)]">
            esc close
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
