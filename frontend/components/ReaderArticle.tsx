/* eslint-disable @next/next/no-img-element */
"use client";

import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useImperativeHandle,
  forwardRef,
} from "react";
import Link from "next/link";
import { ContentItem } from "@/types";
import { searchAPI, highlightsAPI, contentAPI } from "@/lib/api";
import { sanitizeContentHtml } from "@/lib/bionicReading";
import { getIngestIssue } from "@/lib/ingestErrors";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { useReadingSettings } from "@/contexts/ReadingSettingsContext";
import HighlightToolbar from "./HighlightToolbar";
import SequentialRetroLoader from "./SequentialRetroLoader";
import HighlightRenderer from "./HighlightRenderer";
import BlockList, { BlockListRef } from "./editor/BlockList";

interface ExtendedSelection {
  text: string;
  startOffset: number;
  endOffset: number;
  position: { x: number; y: number };
  existingHighlightId?: string;
  existingColor?: string;
  existingNote?: string;
}

interface ReaderArticleProps {
  content: ContentItem;
  onStatusChange: (updates: {
    is_read?: boolean;
    is_archived?: boolean;
    read_position?: number;
    full_text?: string;
    is_public?: boolean;
  }) => void;
  embedded?: boolean;
  focusModeEnabled?: boolean;
  onShowConnections?: () => void;
  onHighlightsChange?: (
    highlights: Array<{
      id: string;
      text: string;
      start_offset: number;
      end_offset: number;
      color: string;
      note?: string;
    }>,
  ) => void;
}

export interface ReaderArticleHandle {
  highlights: Array<{
    id: string;
    text: string;
    start_offset: number;
    end_offset: number;
    color: string;
    note?: string;
  }>;
  refreshHighlights: (newHighlightId?: string) => Promise<void>;
  scrollToHighlight: (
    highlight: {
      id: string;
      text: string;
      start_offset: number;
      end_offset: number;
      color: string;
      note?: string;
    },
    clickedElement?: HTMLElement,
  ) => void;
  isEditing: boolean;
  isSaving: boolean;
  highlightsLoading: boolean;
  handleSaveChanges: () => Promise<void>;
  setIsEditing: (v: boolean) => void;
  savedScrollPosition: React.RefObject<number>;
}

const ReaderArticle = forwardRef<ReaderArticleHandle, ReaderArticleProps>(
  function ReaderArticle(
    {
      content,
      onStatusChange,
      embedded = false,
      focusModeEnabled = false,
      onHighlightsChange,
      onShowConnections,
    },
    ref,
  ) {
    // Get user for public profile check
    const { user } = useAuth();

    // Use global theme context
    useTheme();

    // Use reading settings from context
    const { settings } = useReadingSettings();

    // Refs
    const contentRef = useRef<HTMLDivElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const editorRef = useRef<BlockListRef>(null);
    const savedScrollPosition = useRef(0);
    const similarArticlesRef = useRef<HTMLDivElement>(null);
    const scrollPositionBeforeSimilar = useRef<number>(0);
    const readPositionRef = useRef(content.read_position ?? 0);
    const onStatusChangeRef = useRef(onStatusChange);
    // Keep refs in sync with latest props so effects don't need them as deps
    readPositionRef.current = content.read_position ?? 0;
    onStatusChangeRef.current = onStatusChange;

    // Similar articles state
    const [showSimilar, setShowSimilar] = useState(false);
    const [similarArticles, setSimilarArticles] = useState<
      Array<{
        item: ContentItem;
        similarity_score: number;
      }>
    >([]);
    const [loadingSimilar, setLoadingSimilar] = useState(false);
    const [similarError, setSimilarError] = useState<string | null>(null);
    const [isFadingOut, setIsFadingOut] = useState(false);

    // New Highlight State for Inline Expansion
    const [newlyCreatedHighlightId, setNewlyCreatedHighlightId] = useState<
      string | null
    >(null);

    // Summary State
    const [summary, setSummary] = useState<string | null>(
      content.summary || null,
    );
    const [loadingSummary, setLoadingSummary] = useState(false);
    const [showSummary, setShowSummary] = useState(!!content.summary);

    // Edit Mode State
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [editTitle, setEditTitle] = useState(content.title || "");
    const [editDescription, setEditDescription] = useState(
      content.description || "",
    );
    const [editAuthor, setEditAuthor] = useState(content.author || "");
    const [editPublishedDate, setEditPublishedDate] = useState(
      content.published_date
        ? new Date(content.published_date).toISOString().split("T")[0]
        : "",
    );
    const [metadataSaved, setMetadataSaved] = useState(false);
    const [isEditingMeta, setIsEditingMeta] = useState(false);

    const [optimisticMeta, setOptimisticMeta] = useState<{
      title?: string | null;
      author?: string | null;
      published_date?: string | null;
    } | null>(null);

    const displayTitle =
      optimisticMeta?.title !== undefined
        ? optimisticMeta.title
        : content.title;
    const displayAuthor =
      optimisticMeta?.author !== undefined
        ? optimisticMeta.author
        : content.author;
    const displayPublishedDate =
      optimisticMeta?.published_date !== undefined
        ? optimisticMeta.published_date
        : content.published_date;

    const estimatedReadingTime = useMemo(() => {
      if (content.reading_time_minutes) return content.reading_time_minutes;
      if (!content.full_text) return null;
      const words = content.full_text
        .replace(/<[^>]*>?/gm, "")
        .split(/\s+/).length;
      return Math.max(1, Math.ceil(words / 200));
    }, [content.reading_time_minutes, content.full_text]);

    // Extraction Confidence State
    const [extractionConfidence, setExtractionConfidence] = useState<{
      score: number;
      label: string;
    } | null>(null);

    // Highlight / selection state
    const [selection, setSelection] = useState<ExtendedSelection | null>(null);
    const [_isCreatingHighlight, _setIsCreatingHighlight] = useState(false);
    const [highlights, setHighlights] = useState<
      Array<{
        id: string;
        text: string;
        start_offset: number;
        end_offset: number;
        color: string;
        note?: string;
      }>
    >([]);
    const [highlightsLoading, setHighlightsLoading] = useState(false);
    const [connectedHighlightIds, setConnectedHighlightIds] = useState<
      Set<string>
    >(new Set());
    const [zoomedImage, setZoomedImage] = useState<string | null>(null);

    // Initialize edit state when content loads
    useEffect(() => {
      if (content) {
        setEditTitle(displayTitle || "");
        setEditDescription(content.description || "");
        setEditAuthor(displayAuthor || "");
        setEditPublishedDate(
          displayPublishedDate
            ? new Date(displayPublishedDate).toISOString().split("T")[0]
            : "",
        );
      }
    }, [content, displayTitle, displayAuthor, displayPublishedDate]);

    // Save Changes Handler
    const handleSaveChanges = useCallback(async () => {
      if (!editorRef.current) return;

      setIsSaving(true);
      try {
        const newHtml = editorRef.current.getHtml();

        const updated = await contentAPI.update(content.id, {
          title: editTitle,
          description: editDescription,
          full_text: newHtml,
        });

        onStatusChange({
          full_text: updated.full_text || newHtml,
        });

        setIsEditing(false);
      } catch (err) {
        console.error("Failed to save changes:", err);
      } finally {
        setIsSaving(false);
      }
    }, [content.id, editTitle, editDescription, onStatusChange]);

    // Save metadata (title, author, published_date) without entering full edit mode
    const handleSaveMetadata = async () => {
      setIsSaving(true);
      try {
        await contentAPI.update(content.id, {
          title: editTitle || undefined,
          author: editAuthor || undefined,
          published_date: editPublishedDate || null,
        });
        setOptimisticMeta({
          title: editTitle || null,
          author: editAuthor || null,
          published_date: editPublishedDate || null,
        });
        setMetadataSaved(true);
        setTimeout(() => setMetadataSaved(false), 2500);
      } catch (err) {
        console.error("Failed to save metadata:", err);
      } finally {
        setIsSaving(false);
      }
    };

    // Parse extraction confidence from HTML content
    useEffect(() => {
      if (!content.full_text) return;

      const confidenceMatch =
        content.full_text.match(
          /meta name="extraction-confidence" content="(\d+)"/,
        ) ||
        content.full_text.match(
          /meta content="(\d+)" name="extraction-confidence"/,
        );
      if (confidenceMatch && confidenceMatch[1]) {
        const score = parseInt(confidenceMatch[1], 10);
        let label = "low";
        if (score >= 80) label = "high";
        else if (score >= 60) label = "medium";
        setExtractionConfidence({ score, label });
      }
    }, [content.full_text]);

    const handleImageZoom = useCallback((src: string) => {
      setZoomedImage(src);
    }, []);

    // Hotkeys: n (highlight with note), b (bold), i (italic)
    useHotkeys({
      n: async (e) => {
        const windowSelection = window.getSelection();
        if (
          !windowSelection ||
          windowSelection.isCollapsed ||
          !windowSelection.toString().trim()
        ) {
          return;
        }
        e.preventDefault();
        try {
          const range = windowSelection.getRangeAt(0);
          const selectedText = windowSelection.toString().trim();
          const container = contentRef.current;
          if (!container) return;
          const preSelectionRange = range.cloneRange();
          preSelectionRange.selectNodeContents(container);
          preSelectionRange.setEnd(range.startContainer, range.startOffset);
          const startOffset = preSelectionRange.toString().length;
          const endOffset = startOffset + selectedText.length;
          const newHighlight = await highlightsAPI.create(content.id, {
            text: selectedText,
            start_offset: startOffset,
            end_offset: endOffset,
            color: "yellow",
          });
          await refreshHighlights(newHighlight.id);
          windowSelection.removeAllRanges();
        } catch (error) {
          console.error("Failed to create highlight with note:", error);
        }
      },
      b: async (e) => {
        e.preventDefault();
        const ephemeralElements = contentRef.current?.querySelectorAll(
          '[data-ephemeral="true"]',
        );
        const ephemeralData: Array<{
          element: Element;
          parent: Node;
          nextSibling: Node | null;
        }> = [];
        ephemeralElements?.forEach((el) => {
          if (el.parentNode) {
            ephemeralData.push({
              element: el,
              parent: el.parentNode,
              nextSibling: el.nextSibling,
            });
            el.parentNode.removeChild(el);
          }
        });
        try {
          if (contentRef.current) {
            contentRef.current.contentEditable = "true";
            document.execCommand("bold");
            contentRef.current.contentEditable = "false";
            const newHtml = contentRef.current.innerHTML;
            const cleanHtml = sanitizeContentHtml(newHtml);
            onStatusChange({ full_text: cleanHtml });
          }
        } catch (err) {
          console.warn("Formatting failed", err);
        } finally {
          ephemeralData.forEach(({ element, parent, nextSibling }) => {
            parent.insertBefore(element, nextSibling);
          });
        }
      },
      i: async (e) => {
        e.preventDefault();
        const ephemeralElements = contentRef.current?.querySelectorAll(
          '[data-ephemeral="true"]',
        );
        const ephemeralData: Array<{
          element: Element;
          parent: Node;
          nextSibling: Node | null;
        }> = [];
        ephemeralElements?.forEach((el) => {
          if (el.parentNode) {
            ephemeralData.push({
              element: el,
              parent: el.parentNode,
              nextSibling: el.nextSibling,
            });
            el.parentNode.removeChild(el);
          }
        });
        try {
          if (contentRef.current) {
            contentRef.current.contentEditable = "true";
            document.execCommand("italic");
            contentRef.current.contentEditable = "false";
            const newHtml = contentRef.current.innerHTML;
            const cleanHtml = sanitizeContentHtml(newHtml);
            onStatusChange({ full_text: cleanHtml });
          }
        } catch (err) {
          console.warn("Formatting failed", err);
        } finally {
          ephemeralData.forEach(({ element, parent, nextSibling }) => {
            parent.insertBefore(element, nextSibling);
          });
        }
      },
    });

    // Fetch highlights
    const refreshHighlights = useCallback(
      async (newHighlightId?: string) => {
        if (newHighlightId) {
          setNewlyCreatedHighlightId(newHighlightId);
          setTimeout(() => setNewlyCreatedHighlightId(null), 2000);
        }
        if (content.id) {
          try {
            setHighlightsLoading(true);
            const [highlightData, connectionData] = await Promise.allSettled([
              highlightsAPI.getByContent(content.id),
              searchAPI.findArticleConnections(content.id),
            ]);

            if (highlightData.status === "fulfilled") {
              setHighlights(highlightData.value);
              onHighlightsChange?.(highlightData.value);
            } else {
              console.error(
                "Failed to fetch highlights:",
                highlightData.reason,
              );
            }

            if (connectionData.status === "fulfilled") {
              const ids = new Set<string>();
              for (const articleConn of connectionData.value) {
                for (const pair of articleConn.highlight_pairs) {
                  ids.add(pair.user_highlight_id);
                }
              }
              setConnectedHighlightIds(ids);
            }
          } catch (error) {
            console.error("Failed to fetch highlights:", error);
          } finally {
            setHighlightsLoading(false);
          }
        }
      },
      [content.id, onHighlightsChange],
    );

    useEffect(() => {
      refreshHighlights();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [content.id]);

    // Track scroll position and save periodically (embedded-aware)
    useEffect(() => {
      let saveTimeout: NodeJS.Timeout;

      const getScrollEl = () => (embedded ? containerRef.current : window);

      const getScrollMetrics = () => {
        if (embedded && containerRef.current) {
          const el = containerRef.current;
          const scrollTop = el.scrollTop;
          const docHeight = el.scrollHeight - el.clientHeight;
          return { scrollTop, docHeight };
        }
        const scrollTop = window.scrollY;
        const docHeight =
          document.documentElement.scrollHeight - window.innerHeight;
        return { scrollTop, docHeight };
      };

      const handleScroll = () => {
        const { scrollTop, docHeight } = getScrollMetrics();
        const scrollPercent = docHeight > 0 ? scrollTop / docHeight : 0;

        clearTimeout(saveTimeout);
        saveTimeout = setTimeout(() => {
          if (Math.abs(scrollPercent - readPositionRef.current) > 0.05) {
            readPositionRef.current = scrollPercent;
            onStatusChangeRef.current({ read_position: scrollPercent });
          }
        }, 1000);
      };

      const scrollEl = getScrollEl();
      scrollEl?.addEventListener("scroll", handleScroll as EventListener);

      return () => {
        scrollEl?.removeEventListener("scroll", handleScroll as EventListener);
        clearTimeout(saveTimeout);
      };
    }, [content.id, embedded]);

    // Restore scroll position when article loads
    useEffect(() => {
      if (content.read_position && content.read_position > 0) {
        const savedPosition = content.read_position;
        setTimeout(() => {
          if (embedded && containerRef.current) {
            const el = containerRef.current;
            const scrollTo =
              (el.scrollHeight - el.clientHeight) * savedPosition;
            el.scrollTo({ top: scrollTo, behavior: "smooth" });
          } else {
            const docHeight =
              document.documentElement.scrollHeight - window.innerHeight;
            const scrollTo = docHeight * savedPosition;
            window.scrollTo({ top: scrollTo, behavior: "smooth" });
          }
        }, 100);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [content.id]);

    // Focus mode effect - scroll-based detection (embedded-aware)
    useEffect(() => {
      const activeFocusMode = embedded ? focusModeEnabled : focusModeEnabled;

      if (!activeFocusMode) {
        const paragraphs = document.querySelectorAll(
          "#reader-content p, #reader-content h2, #reader-content h3, #reader-content h4, #reader-content blockquote, #reader-content ul, #reader-content ol",
        );
        paragraphs.forEach((p) =>
          p.classList.remove("focused", "near-focused"),
        );
        return;
      }

      const checkFocus = () => {
        const paragraphs = Array.from(
          document.querySelectorAll(
            "#reader-content p, #reader-content h2, #reader-content h3, #reader-content h4, #reader-content blockquote, #reader-content ul, #reader-content ol",
          ),
        );

        if (paragraphs.length === 0) return;

        paragraphs.forEach((p, i) => {
          if (!p.hasAttribute("data-para-index")) {
            p.setAttribute("data-para-index", String(i));
          }
        });

        const targetY = window.innerHeight * 0.3;
        let closestIndex = -1;
        let minDistance = Infinity;

        paragraphs.forEach((p, i) => {
          const rect = p.getBoundingClientRect();
          let dist = 0;
          if (rect.top <= targetY && rect.bottom >= targetY) {
            dist = 0;
          } else {
            dist = Math.min(
              Math.abs(rect.top - targetY),
              Math.abs(rect.bottom - targetY),
            );
          }
          if (dist < minDistance) {
            minDistance = dist;
            closestIndex = i;
          }
        });

        if (closestIndex !== -1) {
          paragraphs.forEach((p, i) => {
            p.classList.remove("focused", "near-focused");
            if (i === closestIndex) {
              p.classList.add("focused");
            } else if (Math.abs(i - closestIndex) <= 1) {
              p.classList.add("near-focused");
            }
          });
        }
      };

      const containerEl = containerRef.current;

      if (embedded && containerEl) {
        containerEl.addEventListener("scroll", checkFocus, {
          passive: true,
        });
      } else {
        window.addEventListener("scroll", checkFocus, { passive: true });
      }
      checkFocus();
      setTimeout(checkFocus, 500);

      return () => {
        if (embedded && containerEl) {
          containerEl.removeEventListener("scroll", checkFocus);
        } else {
          window.removeEventListener("scroll", checkFocus);
        }
      };
    }, [focusModeEnabled, content.full_text, highlights, embedded]);

    // Selection / highlight tracking
    useEffect(() => {
      let selectionTimeout: NodeJS.Timeout;

      const handleSelection = (e?: Event) => {
        clearTimeout(selectionTimeout);

        const target = e?.target as HTMLElement | undefined;

        if (target && !document.contains(target)) {
          return;
        }

        const isElement =
          target &&
          target.nodeType === 1 &&
          typeof target.closest === "function";
        if (target && !isElement) return;

        const isToolbarClick =
          isElement &&
          (target?.closest(".highlight-toolbar") ||
            target?.closest("button")?.textContent?.includes("Highlight"));

        if (isToolbarClick) {
          return;
        }

        selectionTimeout = setTimeout(() => {
          const windowSelection = window.getSelection();

          const selectedText = windowSelection?.toString().trim();
          if (!windowSelection || !selectedText || selectedText.length === 0) {
            if (target?.dataset.highlightId) {
              const clickedHighlight = highlights.find(
                (h) => h.id === target.dataset.highlightId,
              );
              if (clickedHighlight) {
                const rect = target.getBoundingClientRect();
                setSelection({
                  text: clickedHighlight.text,
                  startOffset: clickedHighlight.start_offset,
                  endOffset: clickedHighlight.end_offset,
                  position: {
                    x: rect.left + rect.width / 2,
                    y: rect.bottom,
                  },
                  existingHighlightId: clickedHighlight.id,
                  existingColor: clickedHighlight.color,
                });
                return;
              }
            }

            if (!target?.closest(".highlight-tooltip")) {
              setSelection(null);
            }
            return;
          }

          const offsets = getTextOffsets();
          if (!offsets) return;

          const range = windowSelection.getRangeAt(0);
          const rect = range.getBoundingClientRect();

          setSelection({
            text: offsets.selectedText,
            startOffset: offsets.startOffset,
            endOffset: offsets.endOffset,
            position: {
              x: rect.left + rect.width / 2,
              y: rect.bottom,
            },
          });
        }, 100);
      };

      const handleMouseUp = (e: MouseEvent) => handleSelection(e);
      const handleTouchEnd = (e: TouchEvent) => handleSelection(e);

      document.addEventListener("selectionchange", handleSelection);
      document.addEventListener("mouseup", handleMouseUp);
      document.addEventListener("touchend", handleTouchEnd);

      return () => {
        clearTimeout(selectionTimeout);
        document.removeEventListener("selectionchange", handleSelection);
        document.removeEventListener("mouseup", handleMouseUp);
        document.removeEventListener("touchend", handleTouchEnd);
      };
    }, [highlights]);

    const handleFindSimilar = async () => {
      if (similarArticles.length > 0) {
        if (showSimilar) {
          setIsFadingOut(true);
          if (embedded && containerRef.current) {
            containerRef.current.scrollTo({
              top: scrollPositionBeforeSimilar.current,
              behavior: "smooth",
            });
          } else {
            window.scrollTo({
              top: scrollPositionBeforeSimilar.current,
              behavior: "smooth",
            });
          }
          setTimeout(() => {
            setShowSimilar(false);
            setIsFadingOut(false);
          }, 300);
        } else {
          scrollPositionBeforeSimilar.current = embedded
            ? (containerRef.current?.scrollTop ?? 0)
            : window.scrollY;
          setShowSimilar(true);
          setTimeout(() => {
            if (similarArticlesRef.current) {
              const rect = similarArticlesRef.current.getBoundingClientRect();
              const scrollTarget =
                (embedded
                  ? (containerRef.current?.scrollTop ?? 0)
                  : window.scrollY) +
                rect.top -
                100;
              if (embedded && containerRef.current) {
                containerRef.current.scrollTo({
                  top: scrollTarget,
                  behavior: "smooth",
                });
              } else {
                window.scrollTo({ top: scrollTarget, behavior: "smooth" });
              }
            }
          }, 100);
        }
        return;
      }

      try {
        setLoadingSimilar(true);
        setSimilarError(null);
        const results = await searchAPI.findSimilar(content.id);
        setSimilarArticles(results);
        scrollPositionBeforeSimilar.current = embedded
          ? (containerRef.current?.scrollTop ?? 0)
          : window.scrollY;
        setShowSimilar(true);
        setTimeout(() => {
          if (similarArticlesRef.current) {
            const rect = similarArticlesRef.current.getBoundingClientRect();
            const scrollTarget =
              (embedded
                ? (containerRef.current?.scrollTop ?? 0)
                : window.scrollY) +
              rect.top -
              100;
            if (embedded && containerRef.current) {
              containerRef.current.scrollTo({
                top: scrollTarget,
                behavior: "smooth",
              });
            } else {
              window.scrollTo({ top: scrollTarget, behavior: "smooth" });
            }
          }
        }, 100);
      } catch (error: unknown) {
        console.error("Failed to find related articles:", error);

        const isEmbeddingError =
          error &&
          typeof error === "object" &&
          "response" in error &&
          error.response &&
          typeof error.response === "object" &&
          "status" in error.response &&
          error.response.status === 400 &&
          "data" in error.response &&
          error.response.data &&
          typeof error.response.data === "object" &&
          "detail" in error.response.data &&
          typeof error.response.data.detail === "string" &&
          error.response.data.detail.includes("no embedding");

        setSimilarError(
          isEmbeddingError
            ? "This article is still being processed. Please wait a moment and try again."
            : "Failed to find related articles. Please try again later.",
        );
        setSimilarArticles([]);
        setShowSimilar(true);

        setTimeout(() => {
          if (similarArticlesRef.current) {
            const rect = similarArticlesRef.current.getBoundingClientRect();
            const scrollTarget =
              (embedded
                ? (containerRef.current?.scrollTop ?? 0)
                : window.scrollY) +
              rect.top -
              100;
            if (embedded && containerRef.current) {
              containerRef.current.scrollTo({
                top: scrollTarget,
                behavior: "smooth",
              });
            } else {
              window.scrollTo({ top: scrollTarget, behavior: "smooth" });
            }
          }
        }, 100);
      } finally {
        setLoadingSimilar(false);
      }
    };

    const handleGenerateSummary = async () => {
      if (loadingSummary) return;
      try {
        setLoadingSummary(true);
        await contentAPI.summarize(content.id);

        let attempts = 0;
        const interval = setInterval(async () => {
          attempts++;
          if (attempts > 10) {
            clearInterval(interval);
            setLoadingSummary(false);
            return;
          }
          const updated = await contentAPI.getById(content.id);
          if (updated.summary) {
            setSummary(updated.summary);
            setShowSummary(true);
            clearInterval(interval);
            setLoadingSummary(false);
          }
        }, 1000);
      } catch (e) {
        console.error("Summary generation failed", e);
        setLoadingSummary(false);
      }
    };

    const themeClasses =
      "bg-[var(--color-bg-primary)] text-[var(--color-text-primary)]";
    const linkColorClasses =
      "text-[var(--color-accent)] hover:text-[var(--color-accent-hover)]";

    const scrollToHighlight = useCallback(
      (
        highlight: {
          id: string;
          text: string;
          start_offset: number;
          end_offset: number;
          color: string;
          note?: string;
        },
        clickedElement?: HTMLElement,
      ) => {
        const highlightEls = document.querySelectorAll(
          `[data-highlight-id="${highlight.id}"]`,
        );

        if (highlightEls.length > 0) {
          highlightEls.forEach((el) => {
            el.classList.add("ring-2", "ring-blue-500");
            setTimeout(() => {
              el.classList.remove("ring-2", "ring-blue-500");
            }, 1500);
          });

          if (!clickedElement) {
            highlightEls[0].scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
          }
        }
      },
      [],
    );

    const getTextOffsets = () => {
      const windowSelection = window.getSelection();
      if (
        !windowSelection ||
        windowSelection.rangeCount === 0 ||
        windowSelection.toString().trim().length === 0
      )
        return null;

      const range = windowSelection.getRangeAt(0);
      const contentEl = document.getElementById("article-content");

      if (!contentEl) {
        console.warn(
          "Could not find #article-content, offset calculation may be inaccurate",
        );
        return null;
      }

      const walkerFilter: NodeFilter = {
        acceptNode: (node) => {
          if (
            node.parentElement &&
            node.parentElement.classList.contains("heading-anchor")
          ) {
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        },
      };

      const calculateOffset = (node: Node, offsetInNode: number): number => {
        const walker = document.createTreeWalker(
          contentEl,
          NodeFilter.SHOW_TEXT,
          walkerFilter,
        );
        let totalOffset = 0;
        let currentNode: Node | null;

        while ((currentNode = walker.nextNode())) {
          if (currentNode === node) {
            return totalOffset + offsetInNode;
          }
          totalOffset += (currentNode.textContent || "").length;
        }
        return -1;
      };

      const startOffset = calculateOffset(
        range.startContainer,
        range.startOffset,
      );
      const endOffset = calculateOffset(range.endContainer, range.endOffset);

      if (startOffset === -1 || endOffset === -1) {
        console.warn(
          "Could not calculate exact tracking offsets, using fallback",
        );
        return null;
      }

      const walker = document.createTreeWalker(
        contentEl,
        NodeFilter.SHOW_TEXT,
        walkerFilter,
      );
      let fullText = "";
      let node: Node | null;
      while ((node = walker.nextNode())) {
        fullText += node.textContent || "";
      }

      const selectedText = fullText.substring(startOffset, endOffset);

      return {
        selectedText,
        startOffset,
        endOffset,
      };
    };

    const articleContent = useMemo(() => {
      const displayHighlights = highlights;

      return (
        <div
          className={`w-full select-none cursor-default flex justify-center ${embedded ? "px-6" : "px-5 sm:px-6 lg:px-8"}`}
        >
          <div
            className={`w-full ${
              settings.contentWidth === "narrow"
                ? "max-w-2xl"
                : settings.contentWidth === "wide"
                  ? "max-w-3xl"
                  : "max-w-[42rem]"
            }`}
          >
            <div
              ref={contentRef}
              id="reader-content"
              className={`text-[var(--color-text-secondary)] select-text w-full outline-none
            ${
              settings.fontFamily === "serif"
                ? "font-serif-setting"
                : settings.fontFamily === "sans"
                  ? "font-sans-setting"
                  : settings.fontFamily === "merriweather"
                    ? "font-merriweather-setting"
                    : settings.fontFamily === "verdana"
                      ? "font-verdana-setting"
                      : "font-system-setting"
            }
            ${
              settings.fontSize === "small"
                ? "text-small-setting"
                : settings.fontSize === "large"
                  ? "text-large-setting"
                  : "text-medium-setting"
            }
            ${
              settings.lineHeight === "compact"
                ? "line-height-compact"
                : settings.lineHeight === "spacious"
                  ? "line-height-spacious"
                  : "line-height-comfortable"
            }
            ${
              settings.letterSpacing === "tight"
                ? "letter-spacing-tight"
                : settings.letterSpacing === "wide"
                  ? "letter-spacing-wide"
                  : "letter-spacing-normal"
            }
            ${settings.bionicReading ? "bionic-reading" : ""}
            ${focusModeEnabled ? "focus-mode" : ""}
          `}
            >
              {content.full_text ? (
                <HighlightRenderer
                  html={content.full_text}
                  highlights={displayHighlights}
                  onHighlightClick={scrollToHighlight}
                  onImageClick={handleImageZoom}
                  onDeleteHighlight={async (id) => {
                    await highlightsAPI.delete(id);
                    refreshHighlights();
                  }}
                  onUpdateHighlight={refreshHighlights}
                  newlyCreatedHighlightId={newlyCreatedHighlightId}
                  onShowConnections={(_highlightId) => {
                    onShowConnections?.();
                  }}
                  connectedHighlightIds={connectedHighlightIds}
                />
              ) : (
                <div className="text-center py-12 flex flex-col items-center gap-4">
                  <SequentialRetroLoader
                    messages={
                      content.content_type === "pdf"
                        ? [
                            "Scanning layout...",
                            "Identifying columns...",
                            " extracting figures...",
                            "Reflowing text...",
                          ]
                        : [
                            "Connecting to source...",
                            "Extracting content...",
                            "Parsing article...",
                            "Formatting for you...",
                          ]
                    }
                    className="text-[var(--color-accent)] text-lg"
                    interval={2000}
                  />
                  <p className="text-sm text-[var(--color-text-muted)] opacity-70">
                    {getIngestIssue(
                      content.processing_status,
                      content.processing_error,
                      content.original_url,
                    )?.readerMessage || "This might take a few seconds."}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      );
    }, [
      content.full_text,
      content.content_type,
      content.processing_status,
      content.processing_error,
      content.original_url,
      highlights,
      connectedHighlightIds,
      scrollToHighlight,
      focusModeEnabled,
      settings,
      handleImageZoom,
      newlyCreatedHighlightId,
      refreshHighlights,
      embedded,
      onShowConnections,
    ]);

    useImperativeHandle(
      ref,
      () => ({
        highlights,
        refreshHighlights,
        scrollToHighlight,
        isEditing,
        isSaving,
        highlightsLoading,
        handleSaveChanges,
        setIsEditing,
        savedScrollPosition,
      }),
      [
        highlights,
        refreshHighlights,
        scrollToHighlight,
        isEditing,
        isSaving,
        highlightsLoading,
        handleSaveChanges,
      ],
    );

    return (
      <div
        ref={containerRef}
        className={
          embedded
            ? "h-full overflow-y-auto bg-[var(--color-bg-primary)]"
            : `min-h-screen ${themeClasses} transition-colors select-none`
        }
      >
        <HighlightToolbar
          selection={selection}
          contentId={content.id}
          onClose={() => setSelection(null)}
          onOptimisticCreate={(color) => {
            if (selection) {
              setHighlights((prev) => [
                ...prev,
                {
                  id: `temp-${Date.now()}`,
                  text: selection.text,
                  start_offset: selection.startOffset,
                  end_offset: selection.endOffset,
                  color,
                },
              ]);
            }
          }}
          onHighlightCreated={refreshHighlights}
        />

        {/* Article Content */}
        <article
          className={`py-4 ${embedded ? "pt-4" : "pt-28"} pb-32 select-none overflow-x-hidden w-full ${embedded ? "max-w-full mx-0" : "max-w-5xl mx-auto"}`}
        >
          {/* Article Header */}
          <header
            className={`mb-12 relative ${embedded ? "max-w-2xl mx-auto px-6" : "max-w-2xl mx-auto px-5 sm:px-6 lg:px-8"}`}
          >
            {/* Zone 1: Title + pencil edit button */}
            <div className="group/title relative mb-3">
              {isEditing ? (
                <input
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="w-full font-serif font-normal leading-tight text-4xl text-[var(--color-text-primary)] bg-transparent border-b border-[var(--color-border)] focus:outline-none focus:border-[var(--color-accent)]"
                  placeholder="Article Title"
                />
              ) : (
                <h1 className="font-serif font-normal leading-tight text-4xl text-[var(--color-text-primary)] pr-12">
                  {displayTitle || "Untitled Article"}
                </h1>
              )}
              <button
                onClick={() => setIsEditingMeta((v) => !v)}
                className="absolute top-2 right-0 opacity-0 group-hover/title:opacity-40 hover:!opacity-100 transition-opacity text-[var(--color-text-muted)] hover:text-[var(--color-accent)] p-2 -mr-2 -mt-2"
                title="Edit title, author, and date"
              >
                <svg
                  className="w-5 h-5"
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

            {/* Byline — Zone 2 + Zone 3 */}
            <div className="flex flex-col mb-4 font-mono text-xs tracking-tight">
              {/* Zone 2: article attribution */}
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[var(--color-text-muted)]">
                {displayAuthor && (
                  <span
                    className={`text-[var(--color-text-secondary)] ${displayAuthor.includes(",") || displayAuthor.includes(" and ") ? "basis-full mb-0.5" : ""}`}
                  >
                    {displayAuthor}
                  </span>
                )}
                {displayPublishedDate && (
                  <>
                    {displayAuthor &&
                      !(
                        displayAuthor.includes(",") ||
                        displayAuthor.includes(" and ")
                      ) && (
                        <span className="text-[var(--color-text-faint)]">
                          ·
                        </span>
                      )}
                    <span>
                      published{" "}
                      {new Date(displayPublishedDate).toLocaleDateString(
                        undefined,
                        { year: "numeric", month: "short", day: "numeric" },
                      )}
                    </span>
                  </>
                )}
                {!displayAuthor && !displayPublishedDate && (
                  <span className="text-[var(--color-text-faint)] italic">
                    no attribution
                  </span>
                )}
              </div>

              {/* Inline meta edit panel */}
              {isEditingMeta && (
                <div className="mt-3 mb-2 p-3 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-2">
                  <div>
                    <label className="block text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
                      Title
                    </label>
                    <input
                      type="text"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="w-full bg-[var(--color-bg-primary)] border border-[var(--color-border)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent)] font-sans"
                      placeholder="Article title"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
                        Author
                      </label>
                      <input
                        type="text"
                        value={editAuthor}
                        onChange={(e) => setEditAuthor(e.target.value)}
                        className="w-full bg-[var(--color-bg-primary)] border border-[var(--color-border)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent)] font-sans"
                        placeholder="Author name"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
                        Published
                      </label>
                      <input
                        type="date"
                        value={editPublishedDate}
                        onChange={(e) => setEditPublishedDate(e.target.value)}
                        className="w-full bg-[var(--color-bg-primary)] border border-[var(--color-border)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent)] font-sans"
                      />
                    </div>
                  </div>
                  <div className="flex items-center justify-end gap-3 pt-1">
                    {metadataSaved && (
                      <span className="text-[10px] font-mono text-[var(--color-accent)]">
                        Saved.
                      </span>
                    )}
                    <button
                      onClick={() => setIsEditingMeta(false)}
                      className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={async () => {
                        await handleSaveMetadata();
                        setIsEditingMeta(false);
                      }}
                      disabled={isSaving}
                      className="text-[10px] font-mono uppercase tracking-widest px-3 py-1 border border-[var(--color-border)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
                    >
                      {isSaving ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              )}

              {/* Zone 3: reader info — added date · read time · confidence · domain */}
              <div className="pt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[var(--color-text-faint)]">
                <span>
                  added{" "}
                  {new Date(content.created_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                  })}
                </span>
                {estimatedReadingTime && (
                  <>
                    <span>·</span>
                    <span>{estimatedReadingTime} min read</span>
                  </>
                )}
                {extractionConfidence && (
                  <>
                    <span>·</span>
                    <span className="relative group/conf inline-flex">
                      <span
                        className={`cursor-help px-1.5 py-0.5 font-bold border uppercase tracking-wider text-[10px] ${
                          extractionConfidence.label === "high"
                            ? "bg-green-100/50 text-green-800 border-green-300"
                            : extractionConfidence.label === "medium"
                              ? "bg-yellow-100/50 text-yellow-800 border-yellow-300"
                              : "bg-red-100/50 text-red-800 border-red-300"
                        }`}
                      >
                        {extractionConfidence.score}%
                      </span>
                      <span className="hidden sm:block pointer-events-none absolute bottom-full left-0 mb-2 w-64 bg-[var(--color-bg-primary)] border border-[var(--color-border)] px-3 py-2 text-[10px] font-mono text-[var(--color-text-secondary)] leading-relaxed shadow-md opacity-0 group-hover/conf:opacity-100 transition-opacity duration-150 z-50 normal-case tracking-normal font-normal">
                        Extraction quality: how completely the article text was
                        captured ({extractionConfidence.score}/100). High ≥ 80
                        means full article; Low &lt; 50 means partial or
                        fallback extraction.
                      </span>
                    </span>
                  </>
                )}
                {user?.is_queue_public && (
                  <>
                    <span>·</span>
                    <button
                      onClick={() =>
                        onStatusChange({ is_public: !content.is_public })
                      }
                      className={`inline-flex items-center gap-1 transition-colors ${
                        content.is_public
                          ? "text-[var(--color-accent)] hover:opacity-70"
                          : "hover:text-[var(--color-text-primary)]"
                      }`}
                      title={
                        content.is_public
                          ? "Publicly visible (Click to make private)"
                          : "Private (Click to make public)"
                      }
                    >
                      {content.is_public ? (
                        <>
                          <svg
                            className="w-3 h-3"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                            />
                          </svg>
                          Public
                        </>
                      ) : (
                        <>
                          <svg
                            className="w-3 h-3"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                            />
                          </svg>
                          Private
                        </>
                      )}
                    </button>
                  </>
                )}
                <span>·</span>
                <a
                  href={content.original_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`inline-flex items-center ${linkColorClasses} hover:opacity-70 transition-opacity truncate max-w-[24ch] sm:max-w-none`}
                >
                  ↗{" "}
                  {(() => {
                    try {
                      return new URL(content.original_url).hostname.replace(
                        /^www\./,
                        "",
                      );
                    } catch {
                      return content.original_url;
                    }
                  })()}
                </a>
              </div>
            </div>

            {/* Thumbnail */}
            {content.thumbnail_url && (
              <div className="mt-4 mb-6">
                <img
                  src={content.thumbnail_url}
                  alt=""
                  className="w-full max-h-[500px] object-cover rounded-sm shadow-sm opacity-90 hover:opacity-100 transition-opacity cursor-zoom-in"
                  onClick={() => handleImageZoom(content.thumbnail_url!)}
                />
              </div>
            )}
          </header>

          {content.content_type === "pdf" ||
          content.content_vertical === "academic" ? (
            isEditing ? (
              <div className="mb-10 max-w-2xl mx-auto px-5 sm:px-6 lg:px-8">
                <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] p-6 rounded-sm relative">
                  <span className="absolute top-0 left-6 -translate-y-1/2 bg-[var(--color-bg-secondary)] px-2 text-xs font-serif italic text-[var(--color-text-muted)] border border-[var(--color-border)] rounded-full">
                    Abstract
                  </span>
                  <textarea
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    className="w-full text-[var(--color-text-secondary)] text-base font-serif leading-relaxed bg-transparent border-none resize-none focus:outline-none"
                    rows={4}
                    placeholder="Abstract or description..."
                  />
                </div>
              </div>
            ) : (
              (content.vertical_metadata?.abstract || content.description) && (
                <div className="mb-10 max-w-2xl mx-auto px-5 sm:px-6 lg:px-8">
                  <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] p-6 rounded-sm relative">
                    <span className="absolute top-0 left-6 -translate-y-1/2 bg-[var(--color-bg-secondary)] px-2 text-xs font-serif italic text-[var(--color-text-muted)] border border-[var(--color-border)] rounded-full">
                      Abstract
                    </span>
                    <p className="text-[var(--color-text-secondary)] text-base font-serif leading-relaxed">
                      {content.vertical_metadata?.abstract ||
                        content.description}
                    </p>
                  </div>
                </div>
              )
            )
          ) : (
            content.description && (
              <div className="w-full flex justify-center px-5 sm:px-6 lg:px-8 mb-8">
                <div
                  className={`w-full ${
                    settings.contentWidth === "narrow"
                      ? "max-w-2xl"
                      : settings.contentWidth === "wide"
                        ? "max-w-3xl"
                        : "max-w-[42rem]"
                  }`}
                >
                  <div className="font-serif border-l-4 border-[var(--color-border)] pl-4 text-[var(--color-text-secondary)] text-lg leading-relaxed">
                    {content.description}
                  </div>
                </div>
              </div>
            )
          )}

          {/* TLDR Summary Section */}
          <div className="w-full flex justify-center px-5 sm:px-6 lg:px-8 mb-12">
            <div
              className={`w-full
              ${
                settings.contentWidth === "narrow"
                  ? "max-w-2xl"
                  : settings.contentWidth === "wide"
                    ? "max-w-3xl"
                    : "max-w-[42rem]"
              }
            `}
            >
              <div className="flex items-center gap-4 mb-4">
                <button
                  onClick={() => {
                    if (summary) {
                      setShowSummary(!showSummary);
                    } else {
                      handleGenerateSummary();
                    }
                  }}
                  disabled={loadingSummary}
                  className={`text-xs px-3 py-1.5 leading-none rounded-none border transition-colors flex items-center gap-2 font-mono uppercase tracking-wider
                    ${
                      showSummary && summary
                        ? "bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] border-[var(--color-text-primary)] hover:bg-transparent hover:text-[var(--color-text-primary)]"
                        : "bg-transparent text-[var(--color-text-primary)] border-[var(--color-text-primary)] hover:bg-[var(--color-text-primary)] hover:text-[var(--color-bg-primary)]"
                    }`}
                >
                  {loadingSummary ? (
                    <>
                      <span className="inline-block w-2.5 h-4 bg-[var(--color-text-primary)] animate-blink align-text-bottom mr-1"></span>
                      Summarizing_
                    </>
                  ) : summary ? (
                    showSummary ? (
                      "Hide TL;DR"
                    ) : (
                      "Show TL;DR"
                    )
                  ) : (
                    "Generate TL;DR"
                  )}
                </button>
              </div>

              {showSummary && summary && (
                <div className="relative p-6 border-2 border-[var(--color-text-primary)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] shadow-[4px_4px_0px_0px_var(--color-text-primary)] rounded-none">
                  <div
                    className={`
                      ${
                        settings.fontFamily === "serif"
                          ? "font-serif-setting"
                          : settings.fontFamily === "sans"
                            ? "font-sans-setting"
                            : settings.fontFamily === "merriweather"
                              ? "font-merriweather-setting"
                              : settings.fontFamily === "verdana"
                                ? "font-verdana-setting"
                                : "font-system-setting"
                      }
                      ${settings.lineHeight === "compact" ? "line-height-compact" : settings.lineHeight === "spacious" ? "line-height-spacious" : "line-height-comfortable"}
                    `}
                  >
                    <ul className="list-disc pl-5 space-y-4 marker:text-[var(--color-text-primary)]">
                      {summary.split("\n").map((line, i) => {
                        let clean = line
                          .replace(/[\u200B-\u200D\uFEFF]/g, "")
                          .replace(/\r/g, "")
                          .trim();

                        clean = clean.replace(/^[-•*]\s*/, "");
                        if (!clean) return null;

                        let title: string | null = null;
                        let body: string = clean;

                        const firstMarker = clean.indexOf("**");
                        if (firstMarker !== -1 && firstMarker < 5) {
                          const secondMarker = clean.indexOf(
                            "**",
                            firstMarker + 2,
                          );
                          if (secondMarker !== -1) {
                            title = clean
                              .slice(firstMarker + 2, secondMarker)
                              .trim();
                            const rest = clean.slice(secondMarker + 2);
                            body = rest.replace(/^[\s:\-–—]+/, "").trim();
                          }
                        }

                        return (
                          <li key={i} className="pl-1">
                            {title && (
                              <div className="tldr-title font-bold text-base mb-2 tracking-tight text-[var(--color-text-primary)]">
                                {title}
                              </div>
                            )}
                            <div className="text-sm leading-relaxed opacity-90">
                              {body}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="w-full flex justify-center px-5 sm:px-6 lg:px-8 mb-12">
            {isEditing ? (
              <div className="w-full max-w-2xl">
                <BlockList
                  ref={editorRef}
                  initialHtml={content.full_text || ""}
                  initialScrollTop={savedScrollPosition.current}
                />
              </div>
            ) : (
              articleContent
            )}
          </div>

          {/* End of Article Actions */}
          {content.full_text && (
            <div className="mt-16 max-w-2xl mx-auto px-5 sm:px-6 lg:px-8">
              <div className="flex items-center gap-2">
                <button
                  onClick={() =>
                    onStatusChange({ is_archived: !content.is_archived })
                  }
                  className={`compact-touch text-xs px-2 py-0.5 leading-none rounded-none border transition-colors ${
                    content.is_archived
                      ? "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-accent)]"
                      : "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
                  }`}
                >
                  {content.is_archived ? "Unarchive" : "Archive"}
                </button>

                <button
                  onClick={handleFindSimilar}
                  disabled={loadingSimilar}
                  className={`compact-touch text-xs px-2 py-0.5 leading-none rounded-none border transition-colors flex items-center gap-2
                  ${
                    showSimilar
                      ? "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-accent)]"
                      : "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
                  }
                  ${loadingSimilar ? "opacity-70 cursor-wait" : ""}
                `}
                >
                  {loadingSimilar ? (
                    <span className="font-mono text-xs animate-pulse">
                      Finding related...
                    </span>
                  ) : showSimilar ? (
                    "Hide Related"
                  ) : (
                    "Find Related"
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Similar Articles Section */}
          {showSimilar && (
            <div
              ref={similarArticlesRef}
              className={`mt-8 max-w-2xl mx-auto px-5 sm:px-6 lg:px-8 transition-opacity duration-300 ${
                isFadingOut ? "opacity-0" : "opacity-100"
              }`}
            >
              <h2 className="font-serif text-2xl font-normal mb-6 text-[var(--color-text-primary)]">
                Related Articles
              </h2>

              {similarError ? (
                <div className="p-4 rounded-none border border-[var(--color-accent)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]">
                  <p className="text-sm">{similarError}</p>
                </div>
              ) : similarArticles.length > 0 ? (
                <div className="grid gap-4">
                  {similarArticles.map(({ item, similarity_score }) => (
                    <Link
                      key={item.id}
                      href={`/content/${item.id}`}
                      className="block p-4 rounded-none border border-[var(--color-border)] transition-colors hover:border-[var(--color-accent)]"
                    >
                      <div className="flex items-start gap-4">
                        {item.thumbnail_url && (
                          <img
                            src={item.thumbnail_url}
                            alt=""
                            className="w-20 h-20 object-cover flex-shrink-0 opacity-80 hover:opacity-100 transition-opacity mt-1"
                          />
                        )}
                        <div className="flex-1 min-w-0 flex flex-col pt-1">
                          <h3
                            className={`font-serif font-medium leading-snug line-clamp-2 ${linkColorClasses}`}
                            style={{
                              marginTop: "3px",
                              marginBottom: "10px",
                            }}
                          >
                            {item.title || "Untitled"}
                          </h3>
                          {item.description && (
                            <p className="text-sm text-[var(--color-text-muted)] line-clamp-2 mb-2 leading-relaxed">
                              {item.description}
                            </p>
                          )}
                          <div className="mt-auto flex items-center gap-3 text-xs text-[var(--color-text-faint)]">
                            <span className="text-[var(--color-accent)] font-medium">
                              {Math.round(similarity_score * 100)}% similar
                            </span>
                            {item.reading_time_minutes && (
                              <>
                                <span>•</span>
                                <span>
                                  {item.reading_time_minutes} min read
                                </span>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="p-4 text-[var(--color-text-muted)]">
                  No similar articles found.
                </div>
              )}
            </div>
          )}
        </article>

        {zoomedImage && (
          <ImageZoomModal
            src={zoomedImage}
            onClose={() => setZoomedImage(null)}
          />
        )}
      </div>
    );
  },
);

export default ReaderArticle;

// Separate component for complex zoom logic
function ImageZoomModal({
  src,
  onClose,
}: {
  src: string;
  onClose: () => void;
}) {
  const [scale, setScale] = useState(0.7);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const imageRef = useRef<HTMLImageElement>(null);

  // Lock body scroll while modal is open
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "unset";
    };
  }, []);

  const handleWheel = (e: React.WheelEvent) => {
    e.stopPropagation();
    const delta = e.deltaY * -0.001;
    const newScale = Math.min(Math.max(0.5, scale + delta), 4);
    setScale(newScale);
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsDragging(true);
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      e.stopPropagation();
      setPosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center cursor-zoom-out animate-fade-in backdrop-blur-sm overflow-hidden"
      onClick={onClose}
      onWheel={handleWheel}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onMouseMove={handleMouseMove}
    >
      <div className="relative flex items-center justify-center w-full h-full p-8">
        <img
          ref={imageRef}
          src={src}
          alt="Zoomed"
          className="max-w-full max-h-[85vh] object-contain rounded-sm shadow-2xl transition-transform duration-75 ease-out cursor-move"
          style={{
            transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
          }}
          onClick={(e) => e.stopPropagation()}
          onMouseDown={handleMouseDown}
          draggable={false}
        />
      </div>

      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex items-center gap-4 bg-black/50 backdrop-blur-md px-4 py-2 rounded-full text-white/90">
        <button
          onClick={(e) => {
            e.stopPropagation();
            setScale((s) => Math.max(0.5, s - 0.5));
          }}
          className="hover:text-white p-1"
        >
          -
        </button>
        <span className="text-xs font-mono w-12 text-center">
          {Math.round(scale * 100)}%
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setScale((s) => Math.min(4, s + 0.5));
          }}
          className="hover:text-white p-1"
        >
          +
        </button>
        <div className="w-px h-4 bg-white/20 mx-1" />
        <button
          onClick={(e) => {
            e.stopPropagation();
            setScale(0.7);
            setPosition({ x: 0, y: 0 });
          }}
          className="text-xs hover:text-white"
        >
          Reset
        </button>
      </div>

      <button
        className="absolute top-4 right-4 text-white/70 hover:text-white p-2 transition-colors"
        onClick={onClose}
        aria-label="Close zoom"
      >
        <svg
          className="w-8 h-8"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>
  );
}
