/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { searchAPI } from "@/lib/api";
import RetroLoader from "./RetroLoader";
import SearchModal from "./SearchModal";

interface SearchResult {
  item: {
    id: string;
    title: string;
    description: string | null;
    thumbnail_url: string | null;
    reading_time_minutes: number | null;
  };
  similarity_score: number;
  match_type?: "filter" | "keyword" | "semantic" | "hybrid";
}

const INLINE_LIMIT = 5;

export default function SearchBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const runSearch = useCallback(async (q: string) => {
    try {
      setLoading(true);
      setSearchError(false);
      const data = await searchAPI.semantic(q, { limit: INLINE_LIMIT });
      setResults(data.articles ?? data);
      setShowResults(true);
    } catch {
      setResults([]);
      setSearchError(true);
      setShowResults(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (query.length < 3) {
      setResults([]);
      setShowResults(false);
      return;
    }
    const id = setTimeout(() => runSearch(query), 300);
    return () => clearTimeout(id);
  }, [query, runSearch]);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowResults(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Cmd+K / Ctrl+K global shortcut
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setShowResults(false);
        setModalOpen(true);
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, []);

  const openModal = () => {
    setShowResults(false);
    setModalOpen(true);
  };

  const handleSelectResult = (id: string) => {
    setShowResults(false);
    setQuery("");
    router.push(`/content/${id}`);
  };

  const renderMatchBadge = (_result: SearchResult) => null;

  return (
    <>
      <div ref={searchRef} className="relative w-full">
        {/* Input */}
        <div
          className="relative border border-transparent hover:border-[var(--color-border)] focus-within:!border-[var(--color-accent)] focus-within:bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-secondary)] transition-all px-3"
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
        >
          <svg
            className={`absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-muted)] pointer-events-none transition-opacity ${isHovered || isFocused ? "opacity-100" : "opacity-0"}`}
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
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && query.length >= 3) openModal();
            }}
            placeholder="search"
            className="w-full py-2 bg-transparent rounded-none font-mono text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none border-none outline-none text-center focus:text-left focus:pl-6"
          />

          {loading && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2">
              <RetroLoader
                text="Searching"
                className="text-xs text-[var(--color-accent)] leading-none"
              />
            </div>
          )}
        </div>

        {/* Inline dropdown */}
        {showResults && results.length > 0 && (
          <div className="absolute w-full mt-2 bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-lg z-50">
            {results.map((result) => (
              <button
                key={result.item.id}
                type="button"
                onClick={() => handleSelectResult(result.item.id)}
                className="w-full text-left px-4 py-3 hover:bg-[var(--color-bg-secondary)] transition-colors border-b border-[var(--color-border-subtle)] last:border-b-0"
              >
                <div className="flex items-start gap-3">
                  {result.item.thumbnail_url && (
                    <img
                      src={result.item.thumbnail_url}
                      alt=""
                      className="w-12 h-12 object-cover shrink-0"
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <h4 className="font-medium text-[var(--color-text-primary)] truncate">
                      {result.item.title || "Untitled"}
                    </h4>
                    {result.item.description && (
                      <p className="text-sm text-[var(--color-text-secondary)] line-clamp-2">
                        {result.item.description}
                      </p>
                    )}
                    <div className="flex items-center gap-3 mt-1">
                      {renderMatchBadge(result)}
                      {result.item.reading_time_minutes && (
                        <span className="text-xs text-[var(--color-text-muted)]">
                          {result.item.reading_time_minutes} min read
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </button>
            ))}

            {/* See all results footer */}
            <button
              type="button"
              onClick={openModal}
              className="w-full px-4 py-2.5 text-left flex items-center justify-between bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-tertiary)] transition-colors border-t border-[var(--color-border)]"
            >
              <span className="text-xs font-mono text-[var(--color-text-muted)]">
                See all results for &ldquo;{query}&rdquo;
              </span>
              <span className="text-xs font-mono text-[var(--color-text-muted)]">
                ↵
              </span>
            </button>
          </div>
        )}

        {/* Error */}
        {showResults && !loading && searchError && (
          <div className="absolute w-full mt-2 bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-lg p-3 z-50">
            <div className="border-l-2 border-red-400 dark:border-red-500/60 bg-[var(--color-bg-secondary)] pl-3 pr-3 py-2 flex items-center justify-between gap-3">
              <span className="text-xs text-[var(--color-text-secondary)]">
                Search failed. Try again.
              </span>
              <button
                type="button"
                onClick={() => runSearch(query)}
                className="text-[10px] font-mono tracking-wider text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] transition-colors shrink-0"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {/* No results */}
        {showResults &&
          !loading &&
          !searchError &&
          query.length >= 3 &&
          results.length === 0 && (
            <div className="absolute w-full mt-2 bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-lg p-4 z-50">
              <p className="text-sm text-[var(--color-text-muted)] text-center">
                No results for &ldquo;{query}&rdquo;
              </p>
            </div>
          )}
      </div>

      {/* Command palette modal */}
      {modalOpen && (
        <SearchModal initialQuery={query} onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}
