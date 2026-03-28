"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { listsAPI } from "@/lib/api";
import ListModal from "@/components/ListModal";
import RetroLoader from "@/components/RetroLoader";
import ListBlockCard from "@/components/ListBlockCard";
import { useLists } from "@/contexts/ListsContext";
import Navbar from "@/components/Navbar";

// Type for list with content count (from backend)
interface ListWithCount {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
  content_count: number;
}

type ViewMode = "block" | "index";

export default function ListsPage() {
  const { listCounts, setListCount } = useLists();
  const router = useRouter();

  const [lists, setLists] = useState<ListWithCount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("block");

  const filteredLists = lists
    .filter(
      (list) =>
        list.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (list.description &&
          list.description.toLowerCase().includes(searchQuery.toLowerCase())),
    )
    .sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );

  // Modal state
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [editingList, setEditingList] = useState<ListWithCount | null>(null);

  const fetchLists = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listsAPI.getAll();
      setLists(data);

      // Populate the context with current counts
      data.forEach((list: ListWithCount) => {
        setListCount(list.id, list.content_count);
      });
    } catch (err) {
      console.error("Failed to fetch lists:", err);
      setError("Failed to load lists. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [setListCount]);

  // Fetch lists on component mount
  useEffect(() => {
    fetchLists();
  }, [fetchLists]);

  const handleDeleteList = async (listId: string) => {
    try {
      await listsAPI.delete(listId);
      fetchLists();
    } catch (err) {
      console.error("Failed to delete list:", err);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)]">
        <Navbar />

        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="text-center py-12">
            <div className="text-[var(--color-text-muted)]">
              <RetroLoader text="Loading your lists" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      <Navbar />

      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-8">
          <div className="flex justify-between items-end">
            <h1 className="font-serif text-3xl font-normal text-[var(--color-text-primary)] mt-6">
              My Collections
            </h1>
            <button
              onClick={() => setIsCreateModalOpen(true)}
              className="text-xs px-2 py-1 rounded-none border border-[var(--color-border)] bg-transparent text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors whitespace-nowrap"
            >
              + Create List
            </button>
          </div>
        </div>

        {/* Error message */}
        {error && (
          <div className="border-l-4 border-red-600 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 px-4 py-3 rounded-none mb-6">
            {error}
          </div>
        )}

        {/* Empty state */}
        {lists.length === 0 && !loading && (
          <div className="text-center py-12 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] rounded-none">
            <h3 className="font-serif text-xl font-normal text-[var(--color-text-primary)] mb-2">
              No lists yet
            </h3>
            <p className="text-[var(--color-text-secondary)] mb-6">
              Create your first list to organize your content
            </p>
            <button
              onClick={() => setIsCreateModalOpen(true)}
              className="text-xs px-2 py-1 rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors"
            >
              + Create Your First List
            </button>
          </div>
        )}

        {/* Controls: search + view toggle (matches ContentList pattern) */}
        {lists.length > 0 && (
          <div className="flex items-center gap-3 mb-6">
            {/* Search */}
            <div className="relative flex-1">
              <input
                type="text"
                placeholder="Search lists..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)] transition-colors rounded-none placeholder-[var(--color-text-muted)] text-[var(--color-text-primary)]"
              />
              <svg
                className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-text-muted)]"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            </div>
            {/* View toggle — same icons/style as ContentList */}
            <div className="flex items-center gap-1.5 ml-auto">
              <button
                onClick={() => setViewMode("block")}
                title="Block view"
                className={`p-0.5 transition-colors ${viewMode === "block" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <rect x="2" y="2" width="4" height="4" />
                  <rect x="8" y="2" width="4" height="4" />
                  <rect x="2" y="8" width="4" height="4" />
                  <rect x="8" y="8" width="4" height="4" />
                </svg>
              </button>
              <button
                onClick={() => setViewMode("index")}
                title="Index view"
                className={`p-0.5 transition-colors ${viewMode === "index" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
              >
                <svg
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
        )}

        {/* Lists grid / index */}
        {filteredLists.length > 0 ? (
          viewMode === "block" ? (
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-6">
              {filteredLists.map((list) => (
                <ListBlockCard
                  key={list.id}
                  id={list.id}
                  name={list.name}
                  description={list.description}
                  contentCount={listCounts[list.id] ?? list.content_count}
                  isShared={list.is_shared}
                  onEdit={() => setEditingList(list)}
                  onDelete={() => handleDeleteList(list.id)}
                />
              ))}
            </div>
          ) : (
            /* Index view — matches ContentList style: sticky header + rows */
            <div>
              {/* Header row */}
              <div
                className="py-1 border-b border-[var(--color-text-primary)] font-mono text-[11px] uppercase tracking-wider text-[var(--color-text-muted)] sticky top-0 bg-[var(--color-bg-primary)] z-10 mb-2 hidden sm:grid"
                style={{ gridTemplateColumns: "1fr 5rem 7rem", gap: "0 1rem" }}
              >
                <span>Name</span>
                <span>Items</span>
                <span className="hidden sm:block">Updated</span>
              </div>
              {/* Rows */}
              <div className="flex flex-col">
                {filteredLists.map((list) => {
                  const count = listCounts[list.id] ?? list.content_count;
                  const updatedDate = new Date(
                    list.updated_at,
                  ).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  });
                  return (
                    <div
                      key={list.id}
                      onClick={() => router.push(`/lists/${list.id}`)}
                      className="group grid py-2 border-b border-[var(--color-border-subtle)] cursor-pointer hover:bg-[var(--color-bg-secondary)] transition-colors items-center"
                      style={{
                        gridTemplateColumns: "1fr 5rem 7rem",
                        gap: "0 1rem",
                      }}
                    >
                      <div className="min-w-0">
                        <div className="flex items-baseline gap-2">
                          <span className="font-serif text-base text-[var(--color-text-primary)] group-hover:text-[var(--color-accent)] transition-colors truncate">
                            {list.name}
                          </span>
                          {list.is_shared && (
                            <span className="font-mono text-[9px] uppercase tracking-widest text-[var(--color-text-faint)] border border-[var(--color-border)] px-1 flex-shrink-0">
                              shared
                            </span>
                          )}
                        </div>
                        {list.description && (
                          <p className="font-mono text-xs text-[var(--color-text-faint)] truncate mt-0.5">
                            {list.description}
                          </p>
                        )}
                      </div>
                      <span className="font-mono text-xs text-[var(--color-text-faint)]">
                        {count}
                      </span>
                      <span className="font-mono text-xs text-[var(--color-text-faint)] hidden sm:block">
                        {updatedDate}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )
        ) : (
          lists.length > 0 && (
            <div className="text-center py-12 text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)]">
              No lists match &ldquo;{searchQuery}&rdquo;
            </div>
          )
        )}
      </div>

      {/* Create/Edit List Modal */}
      <ListModal
        isOpen={isCreateModalOpen || editingList !== null}
        onClose={() => {
          setIsCreateModalOpen(false);
          setEditingList(null);
        }}
        onSuccess={() => {
          fetchLists();
        }}
        list={editingList || undefined}
      />
    </div>
  );
}
