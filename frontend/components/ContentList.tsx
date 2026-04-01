"use client";

import {
  useState,
  useEffect,
  useLayoutEffect,
  forwardRef,
  useImperativeHandle,
} from "react";
import { useSearchParams, useRouter } from "next/navigation";
import ContentItem from "./ContentItem";
import ContentIndexItem from "./ContentIndexItem";
import ContentCard from "./ContentCard";
import RetroLoader from "./RetroLoader";
import { contentAPI, listsAPI } from "@/lib/api";
import { ContentItem as ContentItemType, List } from "@/types";
import { useProcessingPolling } from "@/hooks/useProcessingPolling";
import { useLists } from "@/contexts/ListsContext";
import { useHotkeys } from "@/hooks/useHotkeys";
import { FilterDropdownContent } from "./FilterDropdownContent";
import EmptyState from "./EmptyState";
import InlineError from "./InlineError";

/**
 * Helper to ensure safe usage of useLayoutEffect in Next.js SSR
 */
const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

/**
 * Filter type matching the reading status values:
 * - 'all': Show everything (unread, in_progress, read, non-archived)
 * - 'unread': reading_status = 'unread'
 * - 'in_progress': reading_status = 'in_progress'
 * - 'read': reading_status = 'read'
 * - 'archived': is_archived = true (regardless of reading status)
 */
type FilterType = "all" | "unread" | "in_progress" | "read" | "archived";

const CACHE_KEY = "contentListCache";
const CACHE_DURATION = 3600000; // 1 hour

export interface ContentListRef {
  addNewItem: (item: ContentItemType) => void;
}

const ContentList = forwardRef<ContentListRef>((_, ref) => {
  // Toast context for showing success/error messages
  const { incrementListCount, decrementListCount } = useLists();

  // Get URL search params to read filter from URL
  const searchParams = useSearchParams();

  // Helper to get cached data from sessionStorage
  const getCachedData = () => {
    try {
      const cached = sessionStorage.getItem(CACHE_KEY);
      if (!cached) return null;
      const data = JSON.parse(cached);
      const now = Date.now();
      if (data.timestamp && now - data.timestamp < CACHE_DURATION) {
        return data;
      }
      // Cache expired
      sessionStorage.removeItem(CACHE_KEY);
      return null;
    } catch {
      return null;
    }
  };

  // Helper to set cached data in sessionStorage
  const setCachedData = (items: ContentItemType[], total: number) => {
    try {
      sessionStorage.setItem(
        CACHE_KEY,
        JSON.stringify({
          items,
          total,
          timestamp: Date.now(),
        }),
      );
    } catch {
      // Silently fail if sessionStorage is full
    }
  };

  // State for storing the content items from the backend
  const [contents, setContents] = useState<ContentItemType[]>(() => {
    const cached = getCachedData();
    return cached ? cached.items : [];
  });

  // Loading state - true while fetching data
  const [loading, setLoading] = useState(() => {
    const cached = getCachedData();
    return cached ? false : true;
  });

  // Error state - stores error message if fetch fails
  const [error, setError] = useState<string | null>(null);

  // Available Lists for adding content to lists
  const [availableLists, setAvailableLists] = useState<
    Array<{ id: string; name: string }>
  >([]);

  // Current filter selection — read from URL query params or default to 'all'
  const filter = (searchParams.get("filter") as FilterType) || "all";

  // Restore persisted filter on first load (if URL has no ?filter= param)
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!searchParams.get("filter")) {
      const saved = localStorage.getItem("contentListFilter");
      if (saved && saved !== "all") {
        router.replace(`/dashboard?filter=${saved}`);
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Pagination state - backend returns total count
  const [total, setTotal] = useState(() => {
    const cached = getCachedData();
    return cached ? cached.total : 0;
  });

  // Filter dropdown state
  const [filterOpen, setFilterOpen] = useState(false);

  // View Mode: 'list' or 'index'
  const [viewMode, setViewMode] = useState<"list" | "index">("list");

  useEffect(() => {
    const savedView = localStorage.getItem("contentListViewMode");
    if (savedView === "index") setViewMode("index");
  }, []);

  // Sort state (index view only)
  type SortField = "date" | "title" | "author" | "source";
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    const sf = localStorage.getItem("contentListSortField") as SortField | null;
    const sd = localStorage.getItem("contentListSortDir");
    if (sf) setSortField(sf);
    if (sd === "asc" || sd === "desc") setSortDir(sd);
  }, []);

  const toggleSort = (field: SortField) => {
    if (field === sortField) {
      const next = sortDir === "desc" ? "asc" : "desc";
      setSortDir(next);
      localStorage.setItem("contentListSortDir", next);
    } else {
      setSortField(field);
      setSortDir("asc");
      localStorage.setItem("contentListSortField", field);
      localStorage.setItem("contentListSortDir", "asc");
    }
  };

  // Tag filter state
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [availableTags, setAvailableTags] = useState<
    Array<{ tag: string; count: number }>
  >([]);

  /**
   * Expose method to parent component for adding new items optimistically
   */
  useImperativeHandle(ref, () => ({
    addNewItem: (newItem: ContentItemType) => {
      // Add to the beginning of the list (most recent first)
      setContents((prev: ContentItemType[]) => [newItem, ...prev]);
      setTotal((prev: number) => prev + 1);

      // Clear cache so next fetch is fresh
      sessionStorage.removeItem(CACHE_KEY);
    },
  }));

  /**
   * Synchronously load cache before the browser paints to prevent
   * a 1-frame flash of the RetroLoader.
   */
  useIsomorphicLayoutEffect(() => {
    const cachedData = getCachedData();
    if (cachedData) {
      setContents(cachedData.items);
      setTotal(cachedData.total);
      setLoading(false);
    }
  }, []);

  /**
   * useEffect Hook - Runs when component mounts
   * This is where we fetch fresh data if needed, and setup listeners
   */

  useEffect(() => {
    fetchContents();
    fetchAvailableLists();
    fetchAvailableTags();

    // Auto-refresh silently when user switches back to this tab
    const handleFocus = () => {
      fetchContents(true, true); // forceRefresh=true, silent=true
    };

    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [filter]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keyboard navigation state
  const [selectedIndex, setSelectedIndex] = useState<number>(-1);
  const router = useRouter();

  // Reset selection when filter changes
  useEffect(() => {
    setSelectedIndex(-1);
  }, [filter, contents]);

  // Handle hotkeys
  useHotkeys({
    j: () => {
      setSelectedIndex((prev) => {
        const next = Math.min(prev + 1, filteredContents.length - 1);
        scrollToIndex(next);
        return next;
      });
    },
    k: () => {
      setSelectedIndex((prev) => {
        const next = Math.max(prev - 1, 0);
        scrollToIndex(next);
        return next;
      });
    },
    enter: () => {
      if (selectedIndex >= 0 && selectedIndex < filteredContents.length) {
        const item = filteredContents[selectedIndex];
        // Match click logic
        sessionStorage.setItem(
          "contentListScrollPos",
          window.scrollY.toString(),
        );
        router.push(`/content/${item.id}`);
      }
    },
  });

  const scrollToIndex = (index: number) => {
    // Simple logic to scroll element into view if needed
    // We rely on ID matching logic or just heuristics
    // Since we don't hold refsArray easily (without refactoring), we use DOM selector
    if (index < 0) return;
    setTimeout(() => {
      const el = document.getElementById(`content-item-${index}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }, 0);
  };

  /**
   * Synchronously restore scroll position right after DOM renders the articles,
   * but BEFORE the browser paints them. This prevents any visual "jumping".
   */
  useIsomorphicLayoutEffect(() => {
    if (!loading && contents.length > 0) {
      const savedScrollPos = sessionStorage.getItem("contentListScrollPos");
      if (savedScrollPos) {
        const scrollY = parseInt(savedScrollPos, 10);
        window.scrollTo(0, scrollY);
        sessionStorage.removeItem("contentListScrollPos");
      }
    }
  }, [loading, contents.length]);

  /**
   * Polling hook - automatically updates items when processing completes
   * This runs continuously, checking items with status "pending" or "processing"
   */
  useProcessingPolling(contents, (updatedItem) => {
    // When an item finishes processing, update it in our state
    setContents((prevContents) => {
      const newContents = prevContents.map((content) =>
        content.id === updatedItem.id ? updatedItem : content,
      );
      // Update cache so if user navigates back, it doesn't revert to processing
      setCachedData(newContents, total);
      return newContents;
    });

    // Show a toast notification
    if (updatedItem.processing_status === "completed") {
      // Toast removed
    } else if (updatedItem.processing_status === "failed") {
      // Toast removed
    }
  });

  /**
   * Fetches content from the backend API
   * Uses the contentAPI.getAll() helper from lib/api.ts
   * Backend returns: { items: ContentItem[], total: number, skip: number, limit: number }
   *
   * Now includes caching and support for silent background refreshes
   */
  const fetchContents = async (forceRefresh = false, silent = false) => {
    // Cache hit with no force refresh — ensure loading is off and return
    if (!forceRefresh) {
      const cached = getCachedData();
      if (cached) {
        setContents(cached.items);
        setTotal(cached.total);
        setLoading(false);
        return;
      }
    }

    if (!silent) setLoading(true);
    setError(null);

    try {
      const response = await contentAPI.getAll();
      setContents(response.items);
      setTotal(response.total);
      setCachedData(response.items, response.total);
    } catch (err) {
      console.error("Failed to fetch contents:", err);
      if (!silent) setError("Couldn't load your content. Try again.");
    } finally {
      if (!silent) setLoading(false);
    }
  };

  /**
   * Fetches available lists for adding content items to lists
   * Uses the listsAPI.getAll() helper from lib/api.ts
   */
  const fetchAvailableLists = async () => {
    try {
      const lists: List[] = await listsAPI.getAll();
      // Map to simpler format for dropdown
      setAvailableLists(
        lists.map((list) => ({ id: list.id, name: list.name })),
      );
    } catch (err) {
      console.error("Failed to fetch available lists:", err);
      // Silently fail - user can still use other features
    }
  };

  /**
   * Fetches available tags for filtering
   * Uses the contentAPI.getTags() helper from lib/api.ts
   */
  const fetchAvailableTags = async () => {
    try {
      const tags = await contentAPI.getTags();
      setAvailableTags(tags);
    } catch (err) {
      console.error("Failed to fetch available tags:", err);
      // Silently fail - tag filtering still works without counts
    }
  };

  /**
   * Handles marking an item as read/unread or archived
   * Uses optimistic updates: update UI immediately, revert if API call fails
   */
  const handleStatusChange = async (
    id: string,
    updates: { is_read?: boolean; is_archived?: boolean; is_public?: boolean },
  ) => {
    // Save the old state in case we need to revert
    const previousContents = [...contents];

    try {
      // OPTIMISTIC UPDATE: Update UI immediately for better UX
      setContents((prevContents) =>
        prevContents.map((content) =>
          content.id === id ? { ...content, ...updates } : content,
        ),
      );

      // Call the backend to persist the change - get the updated item
      const updatedContent = await contentAPI.update(id, updates);

      // Update with the backend response to ensure reading_status is correct
      setContents((prevContents) => {
        const updated = prevContents.map((content) =>
          content.id === id ? updatedContent : content,
        );
        // Update cache
        setCachedData(updated, total);
        return updated;
      });
    } catch (err) {
      console.error("Failed to update content:", err);
      // REVERT on error: restore previous state
      setContents(previousContents);
      setError("Couldn't update item. Try again.");
    }
  };

  /**
   * Handles deleting a content item
   * Also uses optimistic updates for instant feedback
   */
  const handleDelete = async (id: string) => {
    const previousContents = [...contents];

    try {
      // Remove from UI immediately
      setContents(contents.filter((content) => content.id !== id));
      setTotal(total - 1);

      // Call backend to soft delete
      await contentAPI.delete(id);
    } catch (err) {
      console.error("Failed to delete content:", err);
      // Restore on error
      setContents(previousContents);
      setTotal(total + 1);
      setError("Couldn't delete item. Try again.");
    }
  };

  /**
   * Handles updating a content item
   * Updates the item in the contents list when properties change
   */
  const handleUpdate = (updatedContent: ContentItemType) => {
    setContents((prevContents) => {
      const updated = prevContents.map((content) =>
        content.id === updatedContent.id ? updatedContent : content,
      );
      // Update cache
      setCachedData(updated, total);
      return updated;
    });
    // Refresh tags to show new ones or update counts
    fetchAvailableTags();
  };

  /**
   * Handles adding a content item to a list
   */
  const handleAddToList = async (contentId: string, listId: string) => {
    try {
      // Optimistic update - increment count immediately
      incrementListCount(listId);

      await listsAPI.addContent(listId, [contentId]);
    } catch (err) {
      console.error("Failed to add to list:", err);
      // Revert on error - decrement count back
      decrementListCount(listId);
    }
  };

  /**
   * Client-side filtering based on reading_status and optional tag
   * Uses reading_status computed field from backend and tags array
   */
  const filteredContents = (
    contents && Array.isArray(contents) ? contents : []
  ).filter((content) => {
    // First filter by reading status
    let matchesStatus = false;
    switch (filter) {
      case "unread":
        matchesStatus = content.reading_status === "unread";
        break;
      case "in_progress":
        matchesStatus = content.reading_status === "in_progress";
        break;
      case "read":
        matchesStatus = content.reading_status === "read";
        break;
      case "archived":
        matchesStatus = content.reading_status === "archived";
        break;
      default: // 'all'
        matchesStatus = content.reading_status !== "archived"; // Show all non-archived items
    }

    // Then filter by tag if any are selected
    if (!matchesStatus) return false;
    if (selectedTags.length > 0) {
      // Show content that has AT LEAST ONE of the selected tags matching (OR logic)
      return (content.tags || []).some((tag) => selectedTags.includes(tag));
    }
    return true;
  });

  // Sort filtered contents (index view only; list view preserves API order)
  const sortedContents =
    viewMode === "index"
      ? [...filteredContents].sort((a, b) => {
          let cmp = 0;
          if (sortField === "date") {
            cmp =
              new Date(a.created_at).getTime() -
              new Date(b.created_at).getTime();
          } else if (sortField === "title") {
            cmp = (a.title || "").localeCompare(b.title || "");
          } else if (sortField === "author") {
            cmp = (a.author || "").localeCompare(b.author || "");
          } else if (sortField === "source") {
            const getDomain = (c: typeof a) => {
              try {
                return new URL(c.original_url || "").hostname.replace(
                  /^www\./,
                  "",
                );
              } catch {
                return "";
              }
            };
            cmp = getDomain(a).localeCompare(getDomain(b));
          }
          return sortDir === "asc" ? cmp : -cmp;
        })
      : filteredContents;

  const activeSortClass = (field: SortField) =>
    sortField === field ? "text-[var(--color-accent)]" : "";

  return (
    <div className="space-y-4">
      {/* Error message - shown at top if something went wrong */}
      {error && (
        <InlineError
          message={error}
          onDismiss={() => setError(null)}
          onRetry={() => fetchContents(true)}
          className="mb-4"
        />
      )}

      {/* Contextual Filter Row */}
      <div className="flex items-baseline pl-0 gap-1.5 text-xs text-[var(--color-text-faint)] uppercase tracking-wider mb-4 relative z-20">
        <span>Showing</span>

        <div className="relative inline-block">
          <button
            onClick={() => setFilterOpen(!filterOpen)}
            className="compact-touch font-medium text-[var(--color-text-primary)] border-b border-dotted border-[var(--color-text-secondary)] hover:border-[var(--color-text-primary)] hover:border-solid transition-all flex items-center gap-1 pb-0.5"
          >
            <span className="flex items-center gap-1 lowercase">
              {filter.replace("_", " ")}
              {selectedTags.length > 0 && (
                <>
                  <span className="text-[var(--color-text-muted)]">•</span>
                  <span className="text-[var(--color-accent)]">
                    {selectedTags.map((t) => `#${t}`).join(", ")}
                  </span>
                </>
              )}
            </span>
            <svg
              className={`w-3 h-3 transition-transform ${filterOpen ? "rotate-180" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="square"
                strokeLinejoin="miter"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>

          {filterOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setFilterOpen(false)}
              />
              <div className="absolute left-0 top-full mt-1 w-64 bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-lg z-20 flex flex-col">
                <FilterDropdownContent
                  currentFilter={filter}
                  currentTags={selectedTags}
                  availableTags={availableTags}
                  onSelectFilter={(f) => {
                    localStorage.setItem("contentListFilter", f);
                    setFilterOpen(false);
                  }}
                  onToggleTag={(tag) => {
                    setSelectedTags((prev) => {
                      if (prev.includes(tag)) {
                        return prev.filter((t) => t !== tag);
                      }
                      if (prev.length >= 3) return prev;
                      return [...prev, tag];
                    });
                  }}
                  onClearTags={() => setSelectedTags([])}
                />
              </div>
            </>
          )}
        </div>

        <span>
          ({filteredContents.length} / {total} items)
        </span>

        {/* View Mode Toggle */}
        <div className="flex items-center gap-1.5 ml-auto">
          <button
            onClick={() => {
              setViewMode("list");
              localStorage.setItem("contentListViewMode", "list");
            }}
            title="List view"
            className={`p-0.5 transition-colors ${viewMode === "list" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <rect x="2" y="2" width="10" height="3" />
              <rect x="2" y="7" width="10" height="3" />
            </svg>
          </button>
          <button
            onClick={() => {
              setViewMode("index");
              localStorage.setItem("contentListViewMode", "index");
            }}
            title="Index view"
            className={`p-0.5 transition-colors ${viewMode === "index" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
          >
            <svg
              className="mb-[1px]"
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <line x1="2" y1="3" x2="12" y2="3" />
              <line x1="2" y1="7" x2="12" y2="7" />
              <line x1="2" y1="11" x2="12" y2="11" />
            </svg>
          </button>
        </div>
      </div>

      {/* Content items list */}
      {loading && contents.length === 0 ? (
        <div className="flex justify-center py-12">
          <RetroLoader
            text="Finding your articles"
            className="text-sm text-[var(--color-accent)]"
          />
        </div>
      ) : filteredContents.length === 0 ? (
        <EmptyState
          message={filter === "all" ? "No content yet." : `No ${filter} items.`}
          description={
            filter === "all" ? "Add your first article above." : undefined
          }
        />
      ) : (
        <>
          {/* Mobile: Card layout (only when List Mode) */}
          {viewMode === "list" && (
            <div className="sm:hidden grid gap-4">
              {filteredContents.map((content) => (
                <ContentCard
                  key={content.id}
                  content={content}
                  onStatusChange={handleStatusChange}
                  onDelete={handleDelete}
                  onUpdate={handleUpdate}
                  availableLists={availableLists}
                  onAddToList={(listId) => handleAddToList(content.id, listId)}
                />
              ))}
            </div>
          )}

          {/* Core Layouts Output (List vs Index) */}
          <div
            className={`${viewMode === "list" ? "hidden sm:block" : "block"}`}
          >
            {viewMode === "index" ? (
              /* ---- Index View ---- */
              <div>
                {/* Sortable index headers — matches ContentIndexItem grid exactly */}
                <div
                  className="py-1 px-0 border-b border-[var(--color-text-primary)] font-mono text-[11px] uppercase tracking-wider text-[var(--color-text-muted)] sticky top-0 bg-[var(--color-bg-primary)] z-10 mb-2 whitespace-nowrap hidden sm:grid index-row-grid"
                  style={{
                    gridTemplateColumns:
                      "var(--index-grid-cols, 3.5rem 1fr 8rem 6rem)",
                    gap: "0 1rem",
                  }}
                >
                  <button
                    className={`text-left transition-colors hover:text-[var(--color-accent)] ${activeSortClass("date")}`}
                    onClick={() => toggleSort("date")}
                  >
                    Date
                  </button>
                  <button
                    className={`text-left transition-colors hover:text-[var(--color-accent)] ${activeSortClass("title")}`}
                    onClick={() => toggleSort("title")}
                  >
                    Title
                  </button>
                  <button
                    className={`text-left transition-colors hover:text-[var(--color-accent)] ${activeSortClass("author")}`}
                    onClick={() => toggleSort("author")}
                  >
                    Author
                  </button>
                  <button
                    className={`text-left transition-colors hover:text-[var(--color-accent)] hidden sm:block ${activeSortClass("source")}`}
                    onClick={() => toggleSort("source")}
                  >
                    Source
                  </button>
                </div>

                {/* Index Items */}
                <div className="flex flex-col">
                  {sortedContents.map((content, idx) => (
                    <ContentIndexItem
                      key={content.id}
                      id={`content-item-${idx}`}
                      isSelected={idx === selectedIndex}
                      content={content}
                      onStatusChange={handleStatusChange}
                      onDelete={handleDelete}
                      onUpdate={handleUpdate}
                      availableLists={availableLists}
                      onAddToList={(listId) =>
                        handleAddToList(content.id, listId)
                      }
                    />
                  ))}
                </div>
              </div>
            ) : (
              /* ---- Standard Large Card List ---- */
              <div className="divide-y divide-[var(--color-border-subtle)]">
                {filteredContents.map((content, idx) => (
                  <ContentItem
                    key={content.id}
                    id={`content-item-${idx}`}
                    isSelected={idx === selectedIndex}
                    content={content}
                    onStatusChange={handleStatusChange}
                    onDelete={handleDelete}
                    onUpdate={handleUpdate}
                    availableLists={availableLists}
                    onAddToList={(listId) =>
                      handleAddToList(content.id, listId)
                    }
                  />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
});

ContentList.displayName = "ContentList";

export default ContentList;
