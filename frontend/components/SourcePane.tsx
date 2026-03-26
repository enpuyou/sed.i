"use client";

import { useState, useEffect, useCallback } from "react";
import { listsAPI } from "@/lib/api";
import { ContentItem, Highlight } from "@/types";

interface SourcePaneProps {
    listId: string;
    onInsertHighlight: (text: string) => void;
}

type PaneView = "list" | "article";

interface TabHistoryEntry {
    id: string;
    title: string;
}

const HIGHLIGHT_COLORS: Record<string, string> = {
    yellow: "var(--highlight-yellow)",
    green: "var(--highlight-green)",
    blue: "var(--highlight-blue)",
    pink: "var(--highlight-pink)",
    purple: "var(--highlight-purple)",
};

export default function SourcePane({
    listId,
    onInsertHighlight,
}: SourcePaneProps) {
    const [articles, setArticles] = useState<ContentItem[]>([]);
    const [highlights, setHighlights] = useState<Highlight[]>([]);
    const [selectedArticle, setSelectedArticle] = useState<ContentItem | null>(null);
    const [view, setView] = useState<PaneView>("list");
    const [highlightsOnly, setHighlightsOnly] = useState(false);
    const [tabHistory, setTabHistory] = useState<TabHistoryEntry[]>([]);
    const [loading, setLoading] = useState(true);

    const fetchData = useCallback(async () => {
        try {
            const [contentData, highlightData] = await Promise.all([
                listsAPI.getContent(listId),
                listsAPI.getHighlights(listId),
            ]);
            setArticles(contentData);
            setHighlights(highlightData);
        } catch (err) {
            console.error("SourcePane: failed to load list data:", err);
        } finally {
            setLoading(false);
        }
    }, [listId]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const openArticle = (article: ContentItem) => {
        setSelectedArticle(article);
        setView("article");
        // Maintain session tab history (max 3)
        setTabHistory((prev) => {
            const filtered = prev.filter((t) => t.id !== article.id);
            return [{ id: article.id, title: article.title || article.original_url }, ...filtered].slice(0, 3);
        });
    };

    const goBack = () => {
        setView("list");
        setSelectedArticle(null);
        setHighlightsOnly(false);
    };

    const getArticleHighlights = (articleId: string) =>
        highlights.filter((h) => h.content_item_id === articleId);

    const getHighlightCount = (articleId: string) =>
        getArticleHighlights(articleId).length;

    const getDomain = (url: string) => {
        try {
            return new URL(url).hostname.replace("www.", "");
        } catch {
            return url;
        }
    };

    if (loading) {
        return (
            <div className="source-pane source-pane-loading">
                <p className="source-loading-text">Loading sources…</p>
            </div>
        );
    }

    return (
        <div className="source-pane">
            {/* Session tab row — shows last 3 opened articles */}
            {tabHistory.length > 0 && view === "article" && (
                <div className="source-tabs">
                    {tabHistory.map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => {
                                const art = articles.find((a) => a.id === tab.id);
                                if (art) openArticle(art);
                            }}
                            className={`source-tab compact-touch ${selectedArticle?.id === tab.id ? "active" : ""}`}
                            title={tab.title}
                        >
                            <span className="source-tab-text">{tab.title}</span>
                        </button>
                    ))}
                </div>
            )}

            {/* Article List View */}
            {view === "list" && (
                <div className="source-list-view">
                    <div className="source-list-header">
                        <span className="source-list-label">Sources</span>
                        <span className="source-list-count">{articles.length}</span>
                    </div>

                    {articles.length === 0 ? (
                        <div className="source-empty">
                            <p className="source-empty-text">No articles in this list yet.</p>
                        </div>
                    ) : (
                        <div className="source-article-list">
                            {articles.map((article) => {
                                const hCount = getHighlightCount(article.id);
                                return (
                                    <button
                                        key={article.id}
                                        className={`source-article-row compact-touch ${hCount > 0 ? "has-highlights" : ""}`}
                                        onClick={() => openArticle(article)}
                                    >
                                        <div className="source-article-info">
                                            <span className="source-article-title">
                                                {article.title || getDomain(article.original_url)}
                                            </span>
                                            <span className="source-article-domain">
                                                {getDomain(article.original_url)}
                                            </span>
                                        </div>
                                        {hCount > 0 && (
                                            <span className="source-highlight-badge">{hCount}</span>
                                        )}
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}

            {/* Article Reader View */}
            {view === "article" && selectedArticle && (
                <div className="source-reader-view">
                    {/* Reader header */}
                    <div className="source-reader-header">
                        <button onClick={goBack} className="source-back-btn compact-touch">
                            ← All Sources
                        </button>
                        <div className="source-reader-controls">
                            <button
                                onClick={() => setHighlightsOnly((v) => !v)}
                                className={`highlights-toggle compact-touch ${highlightsOnly ? "active" : ""}`}
                                title={highlightsOnly ? "Show full article" : "Highlights only"}
                            >
                                {highlightsOnly ? "Show All" : "Highlights Only"}
                            </button>
                        </div>
                    </div>

                    {/* Article title */}
                    <div className="source-reader-title">
                        <h2 className="source-article-heading">
                            {selectedArticle.title || getDomain(selectedArticle.original_url)}
                        </h2>
                        <a
                            href={selectedArticle.original_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="source-article-link compact-touch"
                        >
                            {getDomain(selectedArticle.original_url)} ↗
                        </a>
                    </div>

                    {/* Content */}
                    <div className={`source-reader-content ${highlightsOnly ? "highlights-only-mode" : ""}`}>
                        {highlightsOnly ? (
                            // Highlights-only: just the highlights as cards
                            <div className="highlights-list">
                                {getArticleHighlights(selectedArticle.id).length === 0 ? (
                                    <p className="no-highlights-msg">No highlights on this article yet.</p>
                                ) : (
                                    getArticleHighlights(selectedArticle.id).map((h) => (
                                        <HighlightCard
                                            key={h.id}
                                            highlight={h}
                                            onInsert={() => onInsertHighlight(h.text)}
                                        />
                                    ))
                                )}
                            </div>
                        ) : (
                            // Full article text with inline highlights
                            <ArticleWithHighlights
                                article={selectedArticle}
                                highlights={getArticleHighlights(selectedArticle.id)}
                                onInsertHighlight={onInsertHighlight}
                            />
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

// Highlight card for "Highlights Only" mode
function HighlightCard({
    highlight,
    onInsert,
}: {
    highlight: Highlight;
    onInsert: () => void;
}) {
    return (
        <div
            className="highlight-card"
            style={{
                borderLeftColor: HIGHLIGHT_COLORS[highlight.color] || HIGHLIGHT_COLORS.yellow,
            }}
        >
            <p className="highlight-card-text">{highlight.text}</p>
            {highlight.note && (
                <p className="highlight-card-note">{highlight.note}</p>
            )}
            <button
                onClick={onInsert}
                className="insert-highlight-btn compact-touch"
                title="Insert into draft"
            >
                Insert into draft →
            </button>
        </div>
    );
}

// Full article text with highlights marked
function ArticleWithHighlights({
    article,
    highlights,
    onInsertHighlight,
}: {
    article: ContentItem;
    highlights: Highlight[];
    onInsertHighlight: (text: string) => void;
}) {
    const text = article.full_text || article.description || "";

    if (!text) {
        return (
            <div className="source-reader-body">
                <p className="source-no-content">
                    Full text not available.{" "}
                    <a
                        href={article.original_url}
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        Open original ↗
                    </a>
                </p>
                {highlights.length > 0 && (
                    <div className="highlights-list">
                        <p className="highlights-section-label">Your highlights:</p>
                        {highlights.map((h) => (
                            <HighlightCard
                                key={h.id}
                                highlight={h}
                                onInsert={() => onInsertHighlight(h.text)}
                            />
                        ))}
                    </div>
                )}
            </div>
        );
    }

    // Build rendered segments: split text by highlight offsets
    const sorted = [...highlights].sort((a, b) => a.start_offset - b.start_offset);
    const segments: React.ReactNode[] = [];
    let cursor = 0;

    sorted.forEach((h, i) => {
        if (h.start_offset > cursor) {
            segments.push(
                <span key={`text-${i}`}>{text.slice(cursor, h.start_offset)}</span>
            );
        }
        const hText = text.slice(h.start_offset, h.end_offset);
        segments.push(
            <mark
                key={h.id}
                className="inline-highlight clickable-highlight"
                style={{
                    backgroundColor: HIGHLIGHT_COLORS[h.color] || HIGHLIGHT_COLORS.yellow,
                    cursor: "pointer",
                }}
                onClick={() => onInsertHighlight(hText)}
                title="Click to insert into draft"
            >
                {hText}
            </mark>
        );
        cursor = h.end_offset;
    });

    if (cursor < text.length) {
        segments.push(<span key="text-end">{text.slice(cursor)}</span>);
    }

    return (
        <div className="source-reader-body source-prose">
            {segments}
        </div>
    );
}
