"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { listsAPI } from "@/lib/api";
import { useLists } from "@/contexts/ListsContext";
import InlineError from "./InlineError";

/**
 * Sidebar Navigation Component
 *
 * Provides navigation links to:
 * - Main content views (All, Unread, Archived)
 * - User's custom lists
 *
 * Shows content counts for quick overview
 * Responsive: collapsible on mobile
 */

interface ListItem {
  id: string;
  name: string;
  content_count: number;
}

export default function Sidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [lists, setLists] = useState<ListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const { listCounts, setListCount } = useLists();

  const fetchLists = useCallback(async () => {
    try {
      setLoading(true);
      setFetchError(false);
      const data = await listsAPI.getAll();
      setLists(data);
      data.forEach((list: ListItem) => {
        setListCount(list.id, list.content_count);
      });
    } catch (err) {
      console.error("Failed to fetch lists:", err);
      setFetchError(true);
    } finally {
      setLoading(false);
    }
  }, [setListCount]);

  useEffect(() => {
    fetchLists();
  }, [fetchLists]);

  /**
   * Helper to determine if a link is active
   * Handles both exact path matches and query parameter matches
   */
  const isActive = (path: string) => {
    // For paths with query params, check if current URL matches
    if (path.includes("?")) {
      // Extract base path and search params
      const [basePath, queryPart] = path.split("?");
      // Check if pathname matches
      if (pathname !== basePath) return false;
      const linkParams = new URLSearchParams(queryPart);

      // Check if all link params match current params
      for (const [key, value] of linkParams.entries()) {
        if (searchParams.get(key) !== value) return false;
      }
      return true;
    }

    // For paths without query params, check exact match and no query params
    return pathname === path && !searchParams.toString();
  };

  /**
   * Base classes for navigation links
   * Active links get left border, inactive get hover effect
   */
  const linkClasses = (path: string) => {
    return `flex items-center justify-between px-4 py-2 text-sm font-medium transition-colors ${
      isActive(path)
        ? "text-[var(--color-text-primary)] border-l-2 border-[var(--color-accent)] -ml-px pl-[calc(1rem-2px)]"
        : "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] pl-4"
    }`;
  };

  return (
    <aside className="hidden lg:block w-64 bg-[var(--color-bg-primary)] border-r border-[var(--color-border)] h-screen sticky top-0 overflow-y-auto">
      <div className="p-4 space-y-6">
        {/* App Title */}
        <div className="px-4">
          <h1
            className="text-2xl font-normal text-[var(--color-text-primary)] tracking-tight"
            style={{ fontFamily: "var(--font-logo)" }}
          >
            sed.i
          </h1>
        </div>

        {/* Main Navigation Links */}
        <nav className="space-y-1">
          <Link href="/dashboard" className={linkClasses("/dashboard")}>
            <span>All Content</span>
          </Link>

          <Link
            href="/dashboard?filter=unread"
            className={linkClasses("/dashboard?filter=unread")}
          >
            <span>Unread</span>
          </Link>

          <Link
            href="/dashboard?filter=archived"
            className={linkClasses("/dashboard?filter=archived")}
          >
            <span>Archived</span>
          </Link>
        </nav>

        {/* Divider */}
        <div className="border-t border-[var(--color-border)]"></div>

        {/* Lists Section */}
        <div className="space-y-2">
          <div className="flex items-center justify-between px-4">
            <h2 className="text-xs text-[var(--color-text-faint)] uppercase tracking-widest font-medium">
              Lists
            </h2>
            <Link
              href="/lists"
              className="text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] text-sm"
            >
              Manage
            </Link>
          </div>

          {/* Lists Loading State */}
          {loading && (
            <div className="px-4 py-2 text-sm text-[var(--color-text-muted)]">
              Finding your lists...
            </div>
          )}

          {/* Lists Error State */}
          {!loading && fetchError && (
            <div className="px-4">
              <InlineError
                message="Couldn't load lists."
                onRetry={fetchLists}
                className="py-1.5"
              />
            </div>
          )}

          {/* Lists Navigation */}
          {!loading && !fetchError && lists.length === 0 && (
            <div className="px-4 py-2 text-sm text-[var(--color-text-muted)]">
              No lists yet.{" "}
              <Link
                href="/lists"
                className="text-[var(--color-accent)] hover:text-[var(--color-accent-hover)]"
              >
                Create one
              </Link>
            </div>
          )}

          {!loading && !fetchError && lists.length > 0 && (
            <nav className="space-y-1">
              {lists.map((list) => (
                <Link
                  key={list.id}
                  href={`/lists/${list.id}`}
                  className={linkClasses(`/lists/${list.id}`)}
                >
                  <span className="truncate">{list.name}</span>
                  <span className="ml-2 text-xs text-[var(--color-text-faint)]">
                    {listCounts[list.id] ?? list.content_count}
                  </span>
                </Link>
              ))}
            </nav>
          )}
        </div>
      </div>
    </aside>
  );
}
