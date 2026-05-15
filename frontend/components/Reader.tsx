"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { ContentItem } from "@/types";
import { useTheme } from "@/contexts/ThemeContext";
import { useReadingSettings } from "@/contexts/ReadingSettingsContext";
import { useHotkeys } from "@/hooks/useHotkeys";
import NowPlaying from "./NowPlaying";
import HighlightsPanel from "./HighlightsPanel";
import ConnectionsPanel from "./ConnectionsPanel";
import ThemeToggle from "./ThemeToggle";
import KeyboardShortcuts from "./KeyboardShortcuts";
import ReaderArticle, { ReaderArticleHandle } from "./ReaderArticle";
import { SHOW_EDIT_ARTICLE } from "@/lib/flags";

interface ReaderProps {
  content: ContentItem;
  onStatusChange: (updates: {
    is_read?: boolean;
    is_archived?: boolean;
    read_position?: number;
    full_text?: string;
    is_public?: boolean;
  }) => void;
  onHighlightCreate?: (highlight: {
    text: string;
    start_offset: number;
    end_offset: number;
    color: string;
  }) => void;
  initialHighlightId?: string;
}

export default function Reader({
  content,
  onStatusChange,
  onHighlightCreate,
  initialHighlightId,
}: ReaderProps) {
  // Use global theme context
  useTheme();

  const { settings, updateSetting } = useReadingSettings();
  const router = useRouter();

  // Ref to access refreshHighlights/scrollToHighlight from ReaderArticle
  const articleRef = useRef<ReaderArticleHandle>(null);

  // Live highlights count — updated via onHighlightsChange callback
  const [highlights, setHighlights] = useState<
    ReaderArticleHandle["highlights"]
  >([]);

  // Navbar auto-hide state
  const [showNavbar, setShowNavbar] = useState(true);
  const [lastScrollY, setLastScrollY] = useState(0);

  // Reading progress state
  const [readProgress, setReadProgress] = useState(0);

  // Panel / UI state
  const [focusMode, setFocusMode] = useState(false);
  const [showHighlightsPanel, setShowHighlightsPanel] = useState(false);
  const [showConnectionsPanel, setShowConnectionsPanel] = useState(false);
  // null = Mode 2 (all highlights); string = Mode 1 (single highlight)
  const [activeHighlightId, setActiveHighlightId] = useState<string | null>(
    null,
  );
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [articleEditUiState, setArticleEditUiState] = useState({
    isEditing: false,
    isSaving: false,
  });

  // TOC state
  const [tocHeadings, setTocHeadings] = useState<
    Array<{ id: string; text: string; level: number }>
  >([]);
  const [activeId, setActiveId] = useState<string>("");
  const [isIdle, setIsIdle] = useState(false);

  const isManualScrolling = useRef(false);
  const tocNavRef = useRef<HTMLDivElement>(null);
  const isUserInteracting = useRef(false);
  const isAutoScrolling = useRef(false);
  const interactionTimeout = useRef<NodeJS.Timeout | undefined>(undefined);

  // Keyboard shortcuts
  useHotkeys({
    esc: () => router.push("/dashboard"),
    h: () => setShowHighlightsPanel((prev) => !prev),
    c: (e) => {
      if (!settings.showConnections) return;
      if (!e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        if (window.innerWidth >= 1280) {
          if (!showConnectionsPanel) {
            // Closed → open in Mode 2
            setActiveHighlightId(null);
            setShowConnectionsPanel(true);
          } else if (activeHighlightId !== null) {
            // Mode 1 → Mode 2
            setActiveHighlightId(null);
          } else {
            // Mode 2 → close
            setShowConnectionsPanel(false);
          }
        }
      }
    },
    f: (e) => {
      if (!e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setFocusMode((prev) => !prev);
      }
    },
    "?": () => setShowShortcuts((v) => !v),
  });

  // Gesture: Two-finger swipe to toggle panels
  useEffect(() => {
    const handleWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) > 30 && Math.abs(e.deltaY) < 30) {
        const screenWidth = window.innerWidth;
        const mouseX =
          (e as WheelEvent & { clientX?: number }).clientX || screenWidth / 2;
        const isLeftHalf = mouseX < screenWidth / 2;

        if (isLeftHalf) {
          if (e.deltaX < 0) {
            if (settings.showConnections) {
              setShowConnectionsPanel(true);
            }
            if (showHighlightsPanel) setShowHighlightsPanel(false);
          } else if (e.deltaX > 0 && showConnectionsPanel) {
            setShowConnectionsPanel(false);
          }
        }

        if (!isLeftHalf) {
          if (e.deltaX > 0) {
            setShowHighlightsPanel(true);
            if (showConnectionsPanel) setShowConnectionsPanel(false);
          } else if (e.deltaX < 0 && showHighlightsPanel) {
            setShowHighlightsPanel(false);
          }
        }
      }
    };

    window.addEventListener("wheel", handleWheel, { passive: true });
    return () => window.removeEventListener("wheel", handleWheel);
  }, [showHighlightsPanel, showConnectionsPanel, settings.showConnections]);

  useEffect(() => {
    if (!settings.showConnections && showConnectionsPanel) {
      setShowConnectionsPanel(false);
    }
  }, [settings.showConnections, showConnectionsPanel]);

  // Scroll listener: navbar auto-hide + readProgress
  useEffect(() => {
    const handleScroll = () => {
      const scrollTop = window.scrollY;
      const docHeight =
        document.documentElement.scrollHeight - window.innerHeight;
      const scrollPercent = docHeight > 0 ? scrollTop / docHeight : 0;

      const SCROLL_THRESHOLD = 10;
      const deltaY = scrollTop - lastScrollY;

      if (Math.abs(deltaY) > SCROLL_THRESHOLD) {
        if (deltaY > 0 && scrollTop > 100) {
          setShowNavbar(false);
        } else if (deltaY < 0 || scrollTop < 50) {
          setShowNavbar(true);
        }
        setLastScrollY(scrollTop);
      }

      setReadProgress(Math.min(scrollPercent * 100, 100));
    };

    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, [lastScrollY]);

  // TOC extraction
  useEffect(() => {
    if (!content.full_text) return;

    const parser = new DOMParser();
    const doc = parser.parseFromString(content.full_text, "text/html");

    const allHeadings: Array<{ id: string; text: string; level: number }> = [];
    const seenIds = new Map<string, number>();

    doc.querySelectorAll("h1, h2, h3, h4").forEach((heading) => {
      let id = heading.id;
      const text = heading.textContent || "";

      if (!id && text) {
        id = text
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "");
      }

      if (id && text) {
        const count = seenIds.get(id) ?? 0;
        seenIds.set(id, count + 1);
        const uniqueId = count === 0 ? id : `${id}-${count + 1}`;
        allHeadings.push({
          id: uniqueId,
          text,
          level: parseInt(heading.tagName.substring(1)),
        });
      }
    });

    const titleNormalized = (content.title || "").toLowerCase().trim();
    let headings = allHeadings.filter((h) => {
      const headingNormalized = h.text.toLowerCase().trim();
      return !(titleNormalized && headingNormalized === titleNormalized);
    });

    if (headings.length > 0) {
      const minLevel = Math.min(...headings.map((h) => h.level));
      const maxLevel = Math.max(...headings.map((h) => h.level));
      const levelRange = maxLevel - minLevel + 1;

      if (levelRange > 3) {
        headings = headings.map((h) => ({
          ...h,
          level: 2 + Math.min(2, h.level - minLevel),
        }));
      } else {
        const levelOffset = minLevel - 2;
        headings = headings.map((h) => ({
          ...h,
          level: h.level - levelOffset,
        }));
      }
    }

    setTocHeadings(headings);
  }, [content.full_text, content.title]);

  // TOC scroll spy
  const updateToc = useCallback(() => {
    if (tocHeadings.length === 0) return;

    const scrollY = window.scrollY;
    const offset = window.innerHeight * 0.3;
    const documentScroll = scrollY + offset;

    let activeIndex = -1;
    for (let i = 0; i < tocHeadings.length; i++) {
      const element = document.getElementById(tocHeadings[i].id);
      if (!element) continue;
      const rect = element.getBoundingClientRect();
      const top = rect.top + scrollY;
      if (top <= documentScroll + 20) {
        activeIndex = i;
      } else {
        break;
      }
    }

    if (activeIndex !== -1) {
      const activeHeading = tocHeadings[activeIndex];
      setActiveId((prev) =>
        prev !== activeHeading.id ? activeHeading.id : prev,
      );

      if (tocNavRef.current && !isUserInteracting.current) {
        const activeEl = document.getElementById(activeHeading.id);
        const nextHeading = tocHeadings[activeIndex + 1];
        const nextEl = nextHeading
          ? document.getElementById(nextHeading.id)
          : null;

        let progress = 0;
        if (activeEl && nextEl) {
          const activeTop = activeEl.getBoundingClientRect().top + scrollY;
          const nextTop = nextEl.getBoundingClientRect().top + scrollY;
          const sectionHeight = nextTop - activeTop;
          const distanceTraveled = documentScroll - activeTop;
          if (sectionHeight > 0) {
            progress = Math.max(
              0,
              Math.min(1, distanceTraveled / sectionHeight),
            );
          }
        }

        const tocActiveLink = tocNavRef.current.querySelector(
          `a[href="#${activeHeading.id}"]`,
        ) as HTMLElement;
        const tocNextLink = tocActiveLink?.nextElementSibling as HTMLElement;

        if (tocActiveLink) {
          const targetCenter =
            tocActiveLink.offsetTop + tocActiveLink.offsetHeight / 2;
          let finalCenter = targetCenter;
          if (tocNextLink) {
            const nextCenter =
              tocNextLink.offsetTop + tocNextLink.offsetHeight / 2;
            finalCenter += (nextCenter - targetCenter) * progress;
          }
          const containerHeight = tocNavRef.current.clientHeight;
          const targetScroll = finalCenter - containerHeight / 2;
          isAutoScrolling.current = true;
          tocNavRef.current.scrollTop = targetScroll;
        }
      }
    }
  }, [tocHeadings]);

  // Main scroll listener for TOC
  useEffect(() => {
    let ticking = false;
    const onScroll = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          updateToc();
          ticking = false;
        });
        ticking = true;
      }
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    setTimeout(updateToc, 100);
    return () => window.removeEventListener("scroll", onScroll);
  }, [updateToc]);

  const handleTocScrollInteraction = useCallback(() => {
    if (isAutoScrolling.current) {
      isAutoScrolling.current = false;
      return;
    }

    isUserInteracting.current = true;
    clearTimeout(interactionTimeout.current);

    interactionTimeout.current = setTimeout(() => {
      isUserInteracting.current = false;
      if (tocNavRef.current) {
        tocNavRef.current.style.scrollBehavior = "smooth";
        updateToc();
        setTimeout(() => {
          if (tocNavRef.current) {
            tocNavRef.current.style.scrollBehavior = "auto";
          }
        }, 300);
      } else {
        updateToc();
      }
    }, 3000);
  }, [updateToc]);

  // Idle timer for TOC
  useEffect(() => {
    let idleTimer: NodeJS.Timeout;

    const resetIdleTimer = () => {
      setIsIdle(false);
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        setIsIdle(true);
      }, 5000);
    };

    window.addEventListener("scroll", resetIdleTimer, { passive: true });
    resetIdleTimer();

    return () => {
      clearTimeout(idleTimer);
      window.removeEventListener("scroll", resetIdleTimer);
    };
  }, []);

  useEffect(() => {
    if (!SHOW_EDIT_ARTICLE || content.content_type !== "pdf") return;

    const syncArticleState = () => {
      const article = articleRef.current;
      if (!article) return;
      setArticleEditUiState((prev) => {
        if (
          prev.isEditing === article.isEditing &&
          prev.isSaving === article.isSaving
        ) {
          return prev;
        }
        return {
          isEditing: article.isEditing,
          isSaving: article.isSaving,
        };
      });
    };

    syncArticleState();
    const intervalId = setInterval(syncArticleState, 150);
    return () => clearInterval(intervalId);
  }, [content.content_type]);

  const themeClasses =
    "bg-[var(--color-bg-primary)] text-[var(--color-text-primary)]";

  const handleHighlightClick = useCallback(
    (highlight: ReaderArticleHandle["highlights"][number]) => {
      articleRef.current?.scrollToHighlight(highlight);
    },
    [],
  );

  // Scroll to source highlight when Mode 1 opens
  useEffect(() => {
    if (!activeHighlightId) return;
    const highlight = highlights.find((h) => h.id === activeHighlightId);
    if (highlight) {
      articleRef.current?.scrollToHighlight(highlight);
    }
  }, [activeHighlightId, highlights]);

  const handleRefreshHighlights = useCallback(() => {
    void articleRef.current?.refreshHighlights();
  }, []);

  return (
    <div
      className={`min-h-screen ${themeClasses} transition-colors select-none`}
    >
      {/* Reading Progress Bar */}
      <div
        className="fixed top-0 left-0 right-0 bg-[var(--color-border-subtle)]"
        style={{ height: "var(--progress-height)" }}
      >
        <div
          className="h-full transition-[width] duration-150"
          style={{
            width: `${readProgress}%`,
            backgroundColor: "var(--color-progress-bar)",
          }}
        />
      </div>

      {/* Sticky Header */}
      <div
        className={`fixed top-0 left-0 right-0 z-10 transition-transform duration-300 ${
          showNavbar ? "translate-y-0" : "-translate-y-full"
        }`}
      >
        <div className="max-w-2xl mx-auto px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            {/* Back Button */}
            <button
              onClick={() => {
                const returnPath =
                  sessionStorage.getItem("readerReturnPath") || "/dashboard";
                sessionStorage.removeItem("readerReturnPath");
                router.push(returnPath);
              }}
              className="compact-touch text-xs px-1.5 py-0.5 sm:px-2 sm:py-0.5 rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors whitespace-nowrap flex-shrink-0 flex items-center"
            >
              ← Back
            </button>

            {/* Reading Controls */}
            <div className="flex items-center gap-2 sm:gap-3">
              {/* Font Size Control */}
              <div className="flex items-center gap-0.5 sm:gap-1">
                {(["small", "medium", "large"] as const).map((size) => (
                  <button
                    key={size}
                    onClick={() => updateSetting("fontSize", size)}
                    className={`compact-touch w-5 h-5 sm:w-6 sm:h-6 flex items-center justify-center rounded-none font-medium transition-colors ${
                      settings.fontSize === size
                        ? "bg-[var(--color-border)] text-[var(--color-text-primary)]"
                        : "bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                    }`}
                  >
                    <span
                      className={
                        size === "small"
                          ? "text-[10px] sm:text-xs"
                          : size === "medium"
                            ? "text-xs sm:text-sm"
                            : "text-sm sm:text-base"
                      }
                    >
                      A
                    </span>
                  </button>
                ))}
              </div>

              <ThemeToggle />

              <div className="flex items-center gap-1 flex-wrap">
                {/* Focus Mode button */}
                <button
                  onClick={() => setFocusMode(!focusMode)}
                  className={`hidden sm:inline-block text-xs px-1.5 py-0.5 sm:px-2 sm:py-0.5 rounded-none border transition-colors ${
                    focusMode
                      ? "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-accent)]"
                      : "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
                  }`}
                  title="Toggle focus mode"
                >
                  Focus
                </button>

                {/* Highlights button */}
                <button
                  onClick={() => setShowHighlightsPanel(!showHighlightsPanel)}
                  className={`hidden xl:inline-block text-xs px-1.5 py-0.5 sm:px-2 sm:py-0.5 rounded-none border transition-colors ${
                    showHighlightsPanel
                      ? "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-accent)]"
                      : "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
                  }`}
                  title={`${showHighlightsPanel ? "Hide" : "Show"} Highlights`}
                >
                  {showHighlightsPanel ? "Hide" : "Show"} Highlights{" "}
                  {highlights.length > 0 && `(${highlights.length})`}
                </button>

                {settings.showConnections && (
                  <button
                    onClick={() =>
                      setShowConnectionsPanel(!showConnectionsPanel)
                    }
                    className={`hidden xl:inline-block text-xs px-1.5 py-0.5 sm:px-2 sm:py-0.5 rounded-none border transition-colors ${
                      showConnectionsPanel
                        ? "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-accent)]"
                        : "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
                    }`}
                    title={`${showConnectionsPanel ? "Hide" : "Show"} Connections`}
                  >
                    {showConnectionsPanel ? "Hide" : "Show"} Connections
                  </button>
                )}

                {/* Edit Mode button - PDF Only, behind feature flag */}
                {SHOW_EDIT_ARTICLE && content.content_type === "pdf" && (
                  <button
                    onClick={() => {
                      const article = articleRef.current;
                      if (!article) return;
                      if (article.isEditing) {
                        article.handleSaveChanges();
                      } else {
                        article.savedScrollPosition.current = window.scrollY;
                        article.setIsEditing(true);
                      }
                    }}
                    disabled={articleEditUiState.isSaving}
                    className={`hidden sm:inline-block text-xs px-1.5 py-0.5 sm:px-2 sm:py-0.5 rounded-none border transition-colors ${
                      articleEditUiState.isEditing
                        ? "bg-[var(--color-accent)] text-[var(--color-text-primary)] border-[var(--color-accent)]"
                        : "bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
                    }`}
                    title="Toggle Edit Mode"
                  >
                    {articleEditUiState.isSaving
                      ? "Saving..."
                      : articleEditUiState.isEditing
                        ? "Save Changes"
                        : "Edit Article"}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Player - Fixed Bottom Left */}
      {settings.showCrates && (
        <div className="hidden md:block fixed bottom-4 left-4 z-40">
          <NowPlaying direction="up" />
        </div>
      )}

      {/* Highlights Panel Sidebar */}
      <div
        className={`hidden xl:flex fixed right-4 top-32 w-80 h-[calc(100vh-16rem)] z-20 overflow-hidden flex-col transition-all duration-300 ease-in-out transform ${
          showHighlightsPanel
            ? "translate-x-0 opacity-100 pointer-events-auto"
            : "translate-x-[120%] opacity-0 pointer-events-none"
        }`}
      >
        <HighlightsPanel
          highlights={highlights}
          onHighlightClick={handleHighlightClick}
          onHighlightDeleted={handleRefreshHighlights}
          onHighlightUpdated={handleRefreshHighlights}
        />
      </div>

      {/* Connections Panel Sidebar */}
      {settings.showConnections && (
        <div
          className={`hidden xl:flex fixed left-4 top-32 w-80 h-[calc(100vh-16rem)] z-20 overflow-hidden flex-col transition-all duration-300 ease-in-out transform ${
            showConnectionsPanel
              ? "translate-x-0 opacity-100 pointer-events-auto"
              : "-translate-x-[120%] opacity-0 pointer-events-none"
          }`}
        >
          <ConnectionsPanel
            contentId={content.id}
            activeHighlightId={activeHighlightId}
            isOpen={showConnectionsPanel}
            onBackToAll={() => setActiveHighlightId(null)}
            onSelectHighlight={(id) => setActiveHighlightId(id)}
            onNavigateToArticle={(id) => router.push(`/content/${id}`)}
          />
        </div>
      )}

      {/* Table of Contents - Desktop Left Sidebar */}
      {tocHeadings.length > 0 &&
        (!settings.showConnections || !showConnectionsPanel) && (
          <div
            ref={tocNavRef}
            onScroll={handleTocScrollInteraction}
            className="hidden xl:block fixed left-8 top-32 w-64 h-[calc(100vh-16rem)] overflow-y-auto pr-4 z-30 opacity-0 animate-fade-in [&::-webkit-scrollbar]:hidden [-ms-overflow-style:'none'] [scrollbar-width:'none']"
            style={{ animationDelay: "0.5s", animationFillMode: "forwards" }}
          >
            <nav className="flex flex-col gap-1.5 mt-4 font-mono tracking-tighter">
              {tocHeadings.map((heading) => {
                const isActive = activeId === heading.id;

                // In idle mode: only show active header
                const shouldShow = !isIdle || isActive;

                // Color logic
                const linkColor =
                  isActive && !isIdle ? "var(--color-accent)" : "#6b7280";
                const linkWeight = isActive ? 500 : 400;

                // Opacity logic
                const opacityClass = !shouldShow
                  ? "opacity-0 pointer-events-none"
                  : isActive
                    ? "opacity-100"
                    : "opacity-80 hover:opacity-100";

                const transformClass = isActive ? "translate-x-1" : "";

                // In idle mode, allow wrapping for active; otherwise truncate
                const textClass =
                  isIdle && isActive
                    ? "whitespace-normal break-words"
                    : "truncate";

                return (
                  <a
                    key={heading.id}
                    href={`#${heading.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      const el = document.getElementById(heading.id);
                      if (el) {
                        isManualScrolling.current = true;
                        const top =
                          el.getBoundingClientRect().top + window.scrollY;
                        const offset = window.innerHeight * 0.3;
                        window.scrollTo({
                          top: top - offset,
                          behavior: "smooth",
                        });
                        setActiveId(heading.id);
                        setTimeout(() => {
                          isManualScrolling.current = false;
                        }, 1000);

                        // Flash the first paragraph after the heading
                        const nextEl = el.nextElementSibling;
                        if (nextEl && nextEl.tagName === "P") {
                          const pElement = nextEl as HTMLElement;
                          const accentColor = getComputedStyle(
                            document.documentElement,
                          )
                            .getPropertyValue("--color-accent")
                            .trim();
                          pElement.style.color = accentColor;
                          setTimeout(() => {
                            pElement.style.color = "";
                          }, 800);
                        }
                      }
                    }}
                    style={{
                      color: linkColor,
                      fontWeight: linkWeight,
                      paddingLeft: `${Math.max(0, heading.level - 2) * 12}px`,
                      fontSize: heading.level === 2 ? "0.9rem" : "0.85rem",
                      lineHeight: isIdle && isActive ? "1.4" : "1.2",
                      transition: "all 500ms ease, opacity 500ms ease",
                    }}
                    className={`
                    toc-link
                    py-0.5 block
                    ${textClass}
                    hover:!text-gray-900 dark:hover:!text-gray-100
                    ${transformClass}
                    ${opacityClass}
                  `}
                    title={heading.text}
                  >
                    {heading.text}
                  </a>
                );
              })}
            </nav>
          </div>
        )}

      {/* Article body */}
      <ReaderArticle
        ref={articleRef}
        content={content}
        onStatusChange={onStatusChange}
        focusModeEnabled={focusMode}
        onHighlightsChange={setHighlights}
        onShowConnections={
          settings.showConnections
            ? (highlightId: string) => {
                setActiveHighlightId(highlightId);
                setShowConnectionsPanel(true);
              }
            : undefined
        }
        onHighlightCreate={onHighlightCreate}
        initialHighlightId={initialHighlightId}
      />

      <KeyboardShortcuts
        isOpen={showShortcuts}
        onClose={() => setShowShortcuts(false)}
        shortcuts={[
          { key: "Esc", desc: "Back to queue" },
          { key: "h", desc: "Toggle highlights panel" },
          { key: "f", desc: "Focus mode" },
          ...(settings.showConnections
            ? [{ key: "c", desc: "Connections panel (desktop)" }]
            : []),
          { key: "?", desc: "Show this help" },
        ]}
      />
    </div>
  );
}
