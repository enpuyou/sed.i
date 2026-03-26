"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { listsAPI, contentAPI, draftsAPI } from "@/lib/api";
import ContentItem from "@/components/ContentItem";
import ContentCard from "@/components/ContentCard";
import RetroLoader from "@/components/RetroLoader";
import AddContentToListModal from "@/components/AddContentToListModal";
import ListModal from "@/components/ListModal";
import WritingWorkspace from "@/components/WritingWorkspace";
import { ContentItem as ContentItemType } from "@/types";
import { useLists } from "@/contexts/ListsContext";
import Navbar from "@/components/Navbar";
import HighlightRenderer from "@/components/HighlightRenderer";

interface ListDetail {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

function getDomain(url: string) {
  try {
    return new URL(url).hostname.replace("www.", "");
  } catch {
    return url;
  }
}

// Inline article reader — uses the same HighlightRenderer as Reader for identical output
function InlineArticleView({
  article,
  onBack,
}: {
  article: ContentItemType;
  onBack: () => void;
}) {
  return (
    <div className="inline-article-view">
      {/* Slim top bar */}
      <div className="inline-article-header">
        <button onClick={onBack} className="inline-article-back compact-touch">
          ← back
        </button>
        <a
          href={article.original_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-article-origin compact-touch"
        >
          {getDomain(article.original_url)} ↗
        </a>
      </div>

      {/* Scrollable body */}
      <div className="inline-article-body">
        <div className="inline-article-prose">
          <h1 className="inline-article-title">
            {article.title || getDomain(article.original_url)}
          </h1>
          {(article.author || article.published_date) && (
            <p className="inline-article-meta">
              {article.author && <span>{article.author}</span>}
              {article.author && article.published_date && <span> · </span>}
              {article.published_date && (
                <span>
                  {new Date(article.published_date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </span>
              )}
            </p>
          )}
          {!article.full_text ? (
            <p className="inline-article-empty">
              Full text not available.{" "}
              <a href={article.original_url} target="_blank" rel="noopener noreferrer">
                Read original ↗
              </a>
            </p>
          ) : (
            <div className="text-[var(--color-text-secondary)] select-text w-full">
              <HighlightRenderer html={article.full_text} highlights={[]} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const DEFAULT_SPLIT = 50; // percent for left pane
const MIN_SPLIT = 25;
const MAX_SPLIT = 75;

export default function ListDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { decrementListCount, incrementListCount } = useLists();

  const listId = params.id as string;

  const [list, setList] = useState<ListDetail | null>(null);
  const [contents, setContents] = useState<ContentItemType[]>([]);
  const [isAddContentModalOpen, setIsAddContentModalOpen] = useState(false);
  const [isEditListModalOpen, setIsEditListModalOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [hasDraft, setHasDraft] = useState(false);

  // Writing pane state
  const [writingOpen, setWritingOpen] = useState(false);
  const [initialDraftContent, setInitialDraftContent] = useState("");
  const [draftLoading, setDraftLoading] = useState(false);
  const [focusMode, setFocusMode] = useState(false);

  // Inline article view (left pane in writing mode)
  const [selectedArticle, setSelectedArticle] =
    useState<ContentItemType | null>(null);

  // Resizable divider
  const [splitPercent, setSplitPercent] = useState(DEFAULT_SPLIT);
  const [dividerVisible, setDividerVisible] = useState(false);
  const dividerHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isDraggingRef = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Export handler ref — set by MarkdownEditor
  const exportHandlerRef = useRef<(() => void) | null>(null);

  const fetchListAndContent = useCallback(
    async (silent = false) => {
      try {
        if (!silent) setLoading(true);
        setError(null);

        const [listData, contentData] = await Promise.all([
          listsAPI.getById(listId),
          listsAPI.getContent(listId),
        ]);

        setList(listData);
        setContents(contentData);

        draftsAPI
          .get(listId)
          .then(() => setHasDraft(true))
          .catch(() => setHasDraft(false));
      } catch (err) {
        console.error("Failed to fetch list:", err);
        setError(
          "Failed to load list. It may not exist or you may not have access.",
        );
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [listId],
  );

  useEffect(() => {
    fetchListAndContent();
  }, [fetchListAndContent]);

  const handleOpenWrite = async () => {
    setDraftLoading(true);
    try {
      const draft = await draftsAPI.get(listId);
      setInitialDraftContent(draft.content ?? "");
    } catch {
      setInitialDraftContent("");
    } finally {
      setDraftLoading(false);
    }
    setWritingOpen(true);
  };

  const handleCloseWrite = useCallback(() => {
    setWritingOpen(false);
    setSelectedArticle(null);
    setFocusMode(false);
    setSplitPercent(DEFAULT_SPLIT);
    draftsAPI
      .get(listId)
      .then(() => setHasDraft(true))
      .catch(() => setHasDraft(false));
  }, [listId]);

  const showDividerBriefly = useCallback(() => {
    setDividerVisible(true);
    if (dividerHideTimerRef.current) clearTimeout(dividerHideTimerRef.current);
    dividerHideTimerRef.current = setTimeout(() => setDividerVisible(false), 1500);
  }, []);

  // Divider drag
  const startDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDraggingRef.current = true;
    setDividerVisible(true);
    if (dividerHideTimerRef.current) clearTimeout(dividerHideTimerRef.current);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMove = (ev: MouseEvent) => {
      if (!isDraggingRef.current || !bodyRef.current) return;
      const rect = bodyRef.current.getBoundingClientRect();
      const pct = ((ev.clientX - rect.left) / rect.width) * 100;
      setSplitPercent(Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, pct)));
    };

    const onUp = () => {
      isDraggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      dividerHideTimerRef.current = setTimeout(() => setDividerVisible(false), 1500);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  const handleRemoveFromList = async (contentId: string) => {
    const previousContents = [...contents];
    try {
      setRemovingId(contentId);
      await new Promise((resolve) => setTimeout(resolve, 800));
      setContents(contents.filter((c) => c.id !== contentId));
      setRemovingId(null);
      decrementListCount(listId);
      await listsAPI.removeContent(listId, [contentId]);
    } catch (err) {
      console.error("Failed to remove from list:", err);
      setContents(previousContents);
      setRemovingId(null);
      incrementListCount(listId);
    }
  };

  const handleStatusChange = async (
    id: string,
    updates: {
      is_read?: boolean;
      is_archived?: boolean;
      read_position?: number;
    },
  ) => {
    const previousContents = [...contents];
    try {
      setContents(
        contents.map((content) =>
          content.id === id ? { ...content, ...updates } : content,
        ),
      );
      await contentAPI.update(id, updates);
    } catch (err) {
      console.error("Failed to update content:", err);
      setContents(previousContents);
    }
  };

  const handleDelete = async (id: string) => {
    const previousContents = [...contents];
    try {
      setContents(contents.filter((content) => content.id !== id));
      await contentAPI.delete(id);
    } catch (err) {
      console.error("Failed to delete content:", err);
      setContents(previousContents);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)]">
        <Navbar />
        <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center py-12">
            <div className="text-[var(--color-text-muted)]">
              <RetroLoader text="Loading list" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error || !list) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)]">
        <Navbar />
        <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center py-12">
            <h2 className="font-serif text-2xl font-normal text-[var(--color-text-primary)] mb-4">
              List Not Found
            </h2>
            <p className="text-[var(--color-text-secondary)] mb-6">
              {error || "This list could not be loaded."}
            </p>
            <button
              onClick={() => router.push("/lists")}
              className="px-6 py-2 text-sm rounded-none bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)] transition-colors"
            >
              Back to Lists
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`list-page-root${writingOpen ? " writing-mode" : ""}`}>
      {/* Navbar transforms when writing is open */}
      <Navbar
        writingMode={writingOpen}
        onWritingClose={handleCloseWrite}
        onWritingFocus={() => setFocusMode((v) => !v)}
        onWritingExport={() => exportHandlerRef.current?.()}
        writingFocusActive={focusMode}
      />

      {writingOpen ? (
        /* ── Split-pane writing layout ── */
        <div className="writing-split-body" ref={bodyRef}>
          {/* Left pane: sources */}
          <div
            className="writing-left-pane"
            style={{ width: `${splitPercent}%` }}
          >
            {selectedArticle ? (
              <InlineArticleView
                article={selectedArticle}
                onBack={() => setSelectedArticle(null)}
              />
            ) : (
              <div className="writing-left-scroll">
                <div className="max-w-2xl mx-auto w-full px-4 sm:px-6">
                  {contents.length === 0 ? (
                    <div className="py-8 text-center">
                      <p className="text-xs text-[var(--color-text-faint)] italic">
                        No articles in this list yet.
                      </p>
                    </div>
                  ) : (
                    <div className="divide-y divide-[var(--color-border-subtle)]">
                      {contents.map((article) => (
                        <div
                          key={article.id}
                          onClickCapture={(e) => {
                            const target = e.target as HTMLElement;
                            if (
                              target.closest("button") ||
                              target.closest("input") ||
                              target.tagName === "BUTTON" ||
                              target.tagName === "INPUT"
                            ) return;
                            e.stopPropagation();
                            e.preventDefault();
                            setSelectedArticle(article);
                          }}
                          style={{ cursor: "pointer" }}
                        >
                          <ContentItem
                            content={article}
                            onStatusChange={handleStatusChange}
                            onDelete={handleDelete}
                            onRemoveFromList={() => handleRemoveFromList(article.id)}
                            navigateTo="#"
                            returnPath={`/lists/${listId}`}
                            isRemoving={article.id === removingId}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Draggable divider */}
          <div
            className={`writing-resize-divider${dividerVisible ? " divider-visible" : ""}`}
            onMouseDown={startDrag}
            onMouseEnter={showDividerBriefly}
            onDoubleClick={() => setSplitPercent(DEFAULT_SPLIT)}
            title="Drag to resize · Double-click to reset"
            role="separator"
          />

          {/* Right pane: editor */}
          <div
            className="writing-right-pane"
            style={{ width: `${100 - splitPercent}%` }}
          >
            <WritingWorkspace
              listId={listId}
              listName={list.name}
              initialContent={initialDraftContent}
              inline
              focusModeEnabled={focusMode}
              onExit={handleCloseWrite}
              onExportReady={(fn) => {
                exportHandlerRef.current = fn;
              }}
            />
          </div>
        </div>
      ) : (
        /* ── Normal list view ── */
        <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {/* Back button */}
          <button
            onClick={() => router.push("/lists")}
            className="text-xs px-2 py-1 rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors"
          >
            ← Back
          </button>

          {/* Title row */}
          <div className="flex justify-between items-start pb-6 mt-4 border-b border-dashed border-[var(--color-border)]">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-2 flex-wrap">
                <h1 className="font-serif text-4xl font-normal text-[var(--color-text-primary)] leading-tight">
                  {list.name}
                </h1>
                <button
                  onClick={() => setIsEditListModalOpen(true)}
                  className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-accent)] transition-colors px-2 py-1"
                  title="Edit list"
                >
                  Edit
                </button>
                {list.is_shared && (
                  <span className="text-[10px] uppercase tracking-widest px-2 py-1 border border-[var(--color-border)] text-[var(--color-text-muted)] mt-1">
                    Shared
                  </span>
                )}
              </div>
              {list.description && (
                <p className="font-serif text-lg text-[var(--color-text-secondary)] mt-2 leading-relaxed">
                  {list.description}
                </p>
              )}
              <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)] mt-4">
                {contents.length} {contents.length === 1 ? "item" : "items"}{" "}
                inside
              </p>
            </div>

            <div className="flex items-center gap-2 ml-4 flex-shrink-0">
              <button
                onClick={handleOpenWrite}
                disabled={draftLoading}
                className="text-xs px-2 py-1 rounded-none border border-[var(--color-accent)] bg-transparent text-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-white transition-colors whitespace-nowrap font-medium disabled:opacity-50"
              >
                {draftLoading
                  ? "Opening…"
                  : hasDraft
                    ? "Continue Writing →"
                    : "Write"}
              </button>
              <button
                onClick={() => setIsAddContentModalOpen(true)}
                className="text-xs px-2 py-1 rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors whitespace-nowrap"
              >
                + Add Content
              </button>
            </div>
          </div>

          {/* Empty state */}
          {contents.length === 0 && (
            <div className="text-center py-12 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] rounded-none mt-6">
              <h3 className="font-serif text-xl font-normal text-[var(--color-text-primary)] mb-4">
                No content yet
              </h3>
              <button
                onClick={() => setIsAddContentModalOpen(true)}
                className="text-xs px-2 py-1 rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors"
              >
                Add Your First Item
              </button>
            </div>
          )}

          {/* Content list */}
          {contents.length > 0 && (
            <>
              {/* Mobile */}
              <div className="sm:hidden grid gap-4 mt-4">
                {contents.map((content) => (
                  <ContentCard
                    key={content.id}
                    content={content}
                    onStatusChange={handleStatusChange}
                    onDelete={handleDelete}
                    onRemoveFromList={() => handleRemoveFromList(content.id)}
                    returnPath={`/lists/${listId}`}
                    isRemoving={content.id === removingId}
                  />
                ))}
              </div>
              {/* Desktop */}
              <div className="hidden sm:block divide-y divide-[var(--color-border-subtle)]">
                {contents.map((content) => (
                  <ContentItem
                    key={content.id}
                    content={content}
                    onStatusChange={handleStatusChange}
                    onDelete={handleDelete}
                    onRemoveFromList={() => handleRemoveFromList(content.id)}
                    returnPath={`/lists/${listId}`}
                    isRemoving={content.id === removingId}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Modals */}
      <AddContentToListModal
        isOpen={isAddContentModalOpen}
        listId={listId}
        onClose={() => setIsAddContentModalOpen(false)}
        onSuccess={() => fetchListAndContent(true)}
        existingContentIds={contents.map((c) => c.id)}
      />
      {list && (
        <ListModal
          isOpen={isEditListModalOpen}
          onClose={() => setIsEditListModalOpen(false)}
          onSuccess={() => fetchListAndContent(true)}
          list={{
            id: list.id,
            name: list.name,
            description: list.description,
            is_shared: list.is_shared,
          }}
        />
      )}
    </div>
  );
}
