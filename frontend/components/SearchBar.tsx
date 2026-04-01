/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { searchAPI } from "@/lib/api";
import RetroLoader from "./RetroLoader";

/**
 * SearchBar Component
 *
 * Provides semantic search functionality:
 * - Debounced search (waits 300ms after user stops typing)
 * - Dropdown with live results
 * - Shows similarity scores
 * - Click result to navigate to article
 */

interface SearchResult {
  item: {
    id: string;
    title: string;
    description: string | null;
    thumbnail_url: string | null;
    reading_time_minutes: number | null;
  };
  similarity_score: number;
}

export default function SearchBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const runSearch = useCallback(async (q: string) => {
    try {
      setLoading(true);
      setSearchError(false);
      const searchResults = await searchAPI.semantic(q);
      setResults(searchResults);
      setShowResults(true);
    } catch (error) {
      console.error("Search failed:", error);
      setResults([]);
      setSearchError(true);
      setShowResults(true);
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Debounced search - waits for user to stop typing
   * This prevents making too many API calls while typing
   */
  useEffect(() => {
    // Don't search if query is too short
    if (query.length < 3) {
      setResults([]);
      setShowResults(false);
      return;
    }

    // Set a timeout to delay the search
    const timeoutId = setTimeout(() => runSearch(query), 300);

    // Cleanup: cancel the timeout if user types again
    return () => clearTimeout(timeoutId);
  }, [query, runSearch]);

  /**
   * Close dropdown when clicking outside
   */
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        searchRef.current &&
        !searchRef.current.contains(event.target as Node)
      ) {
        setShowResults(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  /**
   * Handle selecting a result
   */
  const handleSelectResult = (id: string) => {
    setShowResults(false);
    setQuery("");
    router.push(`/content/${id}`);
  };

  /**
   * Format similarity score as percentage
   */
  const formatScore = (score: number) => {
    return `${Math.round(score * 100)}% match`;
  };

  return (
    <div ref={searchRef} className="relative w-full">
      {/* Search Input - Transparent by default, highlighted on hover/focus */}
      <div
        className="relative border border-transparent hover:border-[var(--color-border)] focus-within:!border-[var(--color-accent)] focus-within:bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-secondary)] transition-all px-3"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {/* Magnifier — absolutely positioned so it doesn't shift text centering */}
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
          placeholder="search"
          className="w-full py-2 bg-transparent rounded-none font-mono text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none border-none outline-none text-center focus:text-left focus:pl-6"
        />

        {/* Retro Loading State — vertically centered */}
        {loading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center">
            <RetroLoader
              text="Searching"
              className="text-xs text-[var(--color-accent)] leading-none"
            />
          </div>
        )}
      </div>

      {/* Results Dropdown */}
      {showResults && results.length > 0 && (
        <div className="absolute w-full mt-2 bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-none shadow-lg max-h-96 overflow-y-auto z-50">
          {results.map((result) => (
            <button
              key={result.item.id}
              type="button"
              onClick={() => handleSelectResult(result.item.id)}
              className="w-full text-left px-4 py-3 hover:bg-[var(--color-bg-secondary)] transition-colors border-b border-[var(--color-border-subtle)] last:border-b-0"
            >
              <div className="flex items-start gap-3">
                {/* Thumbnail (if available) */}
                {result.item.thumbnail_url && (
                  <img
                    src={result.item.thumbnail_url}
                    alt=""
                    className="w-12 h-12 object-cover rounded-none flex-shrink-0"
                  />
                )}

                {/* Content Info */}
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
                    <span className="text-xs text-[var(--color-accent)] font-medium">
                      {formatScore(result.similarity_score)}
                    </span>
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
        </div>
      )}

      {/* Error State */}
      {showResults && !loading && searchError && (
        <div className="absolute w-full mt-2 bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-none shadow-lg p-3 z-50">
          <div className="border-l-2 border-red-400 dark:border-red-500/60 bg-[var(--color-bg-secondary)] pl-3 pr-3 py-2 flex items-center justify-between gap-3">
            <span className="text-xs text-[var(--color-text-secondary)]">
              Search failed. Try again.
            </span>
            <button
              type="button"
              onClick={() => runSearch(query)}
              className="text-[10px] font-mono tracking-wider text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] transition-colors flex-shrink-0"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {/* No Results Message */}
      {showResults &&
        !loading &&
        !searchError &&
        query.length >= 3 &&
        results.length === 0 && (
          <div className="absolute w-full mt-2 bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-none shadow-lg p-4 z-50">
            <p className="text-sm text-[var(--color-text-muted)] text-center">
              No results found for &ldquo;{query}&rdquo;
            </p>
          </div>
        )}
    </div>
  );
}
