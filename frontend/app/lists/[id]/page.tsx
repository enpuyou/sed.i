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
import ReaderArticle from "@/components/ReaderArticle";
import NowPlaying from "@/components/NowPlaying";

interface ListDetail {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

function InlineArticleView({
  article,
  onBack,
  onStatusChange,
}: {
  article: ContentItemType;
  onBack: () => void;
  onStatusChange: (
    id: string,
    updates: {
      is_read?: boolean;
      is_archived?: boolean;
      read_position?: number;
    },
  ) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  return (
    <div className="inline-article-view">
      {/* Invisible left-edge hover zone — hover to reveal ← arrow, click to go back */}
      <div
        className="inline-article-edge"
        onClick={onBack}
        title="Back to sources"
        role="button"
        aria-label="Back to article list"
      />
      <div className="inline-article-body">
        <ReaderArticle
          content={article}
          embedded
          onStatusChange={(updates) => {
            if (
              updates.is_read !== undefined ||
              updates.is_archived !== undefined ||
              updates.read_position !== undefined
            ) {
              onStatusChange(article.id, updates);
            }
          }}
        />
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
  const [editorFullscreen, setEditorFullscreen] = useState(false);

  // Inline article view (left pane in writing mode)
  const [selectedArticle, setSelectedArticle] =
    useState<ContentItemType | null>(null);

  // Resizable divider
  const [splitPercent, setSplitPercent] = useState(DEFAULT_SPLIT);
  const [dividerVisible, setDividerVisible] = useState(false);
  const dividerHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const isDraggingRef = useRef(false);
  const dragMoveHandlerRef = useRef<((ev: MouseEvent) => void) | null>(null);
  const dragUpHandlerRef = useRef<(() => void) | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Export handler ref — set by MarkdownEditor
  const exportHandlerRef = useRef<
    ((format?: "md" | "pdf" | "docx") => void | Promise<void>) | null
  >(null);

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
    setEditorFullscreen(false);
    setSplitPercent(DEFAULT_SPLIT);
    draftsAPI
      .get(listId)
      .then(() => setHasDraft(true))
      .catch(() => setHasDraft(false));
  }, [listId]);

  const showDividerBriefly = useCallback(() => {
    setDividerVisible(true);
    if (dividerHideTimerRef.current) clearTimeout(dividerHideTimerRef.current);
    dividerHideTimerRef.current = setTimeout(
      () => setDividerVisible(false),
      1500,
    );
  }, []);

  const cleanupDrag = useCallback(() => {
    isDraggingRef.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";

    if (dragMoveHandlerRef.current) {
      window.removeEventListener("mousemove", dragMoveHandlerRef.current);
      dragMoveHandlerRef.current = null;
    }

    if (dragUpHandlerRef.current) {
      window.removeEventListener("mouseup", dragUpHandlerRef.current);
      dragUpHandlerRef.current = null;
    }
  }, []);

  // Divider drag
  const startDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      cleanupDrag();

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
        cleanupDrag();
        dividerHideTimerRef.current = setTimeout(
          () => setDividerVisible(false),
          1500,
        );
      };

      dragMoveHandlerRef.current = onMove;
      dragUpHandlerRef.current = onUp;
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [cleanupDrag],
  );

  useEffect(() => {
    return () => {
      cleanupDrag();
      if (dividerHideTimerRef.current) {
        clearTimeout(dividerHideTimerRef.current);
      }
      setDividerVisible(false);
    };
  }, [cleanupDrag]);

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
    <>
      {/* ── Fullscreen chrome: player + top bar, always fixed-positioned ── */}
      {editorFullscreen && (
        <div className="writing-fullscreen-player">
          <NowPlaying />
        </div>
      )}

      {/* ── Normal / split-pane layout (also hosts the single WritingWorkspace instance) ── */}
      <div className={`list-page-root${writingOpen ? " writing-mode" : ""}`}>
        {!editorFullscreen && (
          <Navbar
            writingMode={writingOpen}
            onWritingClose={handleCloseWrite}
            onWritingExport={(fmt) => exportHandlerRef.current?.(fmt)}
          />
        )}

        {writingOpen ? (
          /* ── Split-pane writing layout ── */
          <div className="writing-split-body" ref={bodyRef}>
            {/* Left pane: sources */}
            <div
              className="writing-left-pane"
              style={{ width: `${splitPercent}%` }}
              data-narrow={splitPercent < 38 ? "true" : "false"}
            >
              {selectedArticle ? (
                <InlineArticleView
                  article={selectedArticle}
                  onBack={() => setSelectedArticle(null)}
                  onStatusChange={handleStatusChange}
                />
              ) : (
                <div className="writing-left-scroll">
                  <div className="w-full max-w-2xl px-4 sm:px-6">
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
                              )
                                return;
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
                              onRemoveFromList={() =>
                                handleRemoveFromList(article.id)
                              }
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

            {/* Right pane: editor — becomes fullscreen overlay via CSS class */}
            <div
              className={`writing-right-pane${editorFullscreen ? " writing-right-pane--fullscreen" : ""}`}
              style={
                editorFullscreen
                  ? undefined
                  : { width: `${100 - splitPercent}%` }
              }
            >
              <WritingWorkspace
                listId={listId}
                listName={list.name}
                initialContent={initialDraftContent}
                inline
                onExit={handleCloseWrite}
                onExport={(fmt) => exportHandlerRef.current?.(fmt)}
                onExportReady={(fn) => {
                  exportHandlerRef.current = fn;
                }}
                onFullscreenChange={(fs) => setEditorFullscreen(fs)}
              />
            </div>
          </div>
        ) : (
          /* ── Normal list view ── */
          <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            {/* Breadcrumb */}
            <button
              onClick={() => router.push("/lists")}
              className="font-mono text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors mb-6"
              style={{ color: "var(--color-text-primary)" }}
            >
              ← Lists
            </button>

            {/* Title with hover-reveal edit pencil */}
            <div className="group/title relative mb-4">
              <h1 className="font-serif text-4xl font-normal text-[var(--color-text-primary)] leading-tight pr-10">
                {list.name}
              </h1>
              <button
                onClick={() => setIsEditListModalOpen(true)}
                className="absolute top-1 right-0 opacity-0 group-hover/title:opacity-40 hover:!opacity-100 transition-opacity text-[var(--color-text-muted)] hover:text-[var(--color-accent)] p-2 -mr-2 -mt-1"
                title="Edit list"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
                  />
                </svg>
              </button>
            </div>

            {/* Description */}
            {list.description && (
              <p className="font-serif text-lg text-[var(--color-text-secondary)] leading-relaxed mb-6">
                {list.description}
              </p>
            )}

            {/* Action strip */}
            <div className="flex items-center py-3 gap-3">
              <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] flex-shrink-0">
                {contents.length} {contents.length === 1 ? "item" : "items"}{" "}
                inside
              </span>
              {list.is_shared && (
                <span className="font-mono text-[10px] uppercase tracking-widest px-2 py-0.5 border border-[var(--color-border)] text-[var(--color-text-muted)]">
                  Shared
                </span>
              )}
              <span className="flex-1 font-mono text-[10px] text-[var(--color-border)] select-none overflow-hidden whitespace-nowrap">
                {"·".repeat(40)}
              </span>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={handleOpenWrite}
                  disabled={draftLoading}
                  className="font-mono text-xs px-3 py-1 rounded-none border border-[var(--color-accent)] bg-transparent text-[var(--color-accent)] hover:border-[var(--color-accent-hover)] transition-colors whitespace-nowrap disabled:opacity-50"
                >
                  {draftLoading ? "Opening…" : hasDraft ? "Write →" : "Write"}
                </button>
                <button
                  onClick={() => setIsAddContentModalOpen(true)}
                  className="font-mono text-xs px-3 py-1 rounded-none border border-[var(--color-border)] bg-transparent text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors whitespace-nowrap"
                >
                  + Add
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
    </>
  );
}
