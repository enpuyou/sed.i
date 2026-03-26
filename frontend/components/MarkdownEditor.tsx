"use client";

import { useEditor, EditorContent } from "@tiptap/react";
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { Markdown } from "tiptap-markdown";
import { useEffect, useRef, useCallback, useState } from "react";
import { TypewriterScrolling } from "@/components/editor/TypewriterScrolling";
import { FocusMode, toggleFocusMode } from "@/components/editor/FocusMode";
import WritingStatusBar from "@/components/WritingStatusBar";
import { draftsAPI } from "@/lib/api";

type SaveStatus = "idle" | "saving" | "saved" | "error";

interface MarkdownEditorProps {
    listId: string;
    listName: string;
    initialContent: string;
    focusModeEnabled?: boolean;
    onWordCountChange?: (count: number) => void;
    onInsertText?: (handler: (text: string) => void) => void;
    onExportReady?: (fn: () => void) => void;
}

interface FloatingToolbarPos {
    top: number;
    left: number;
}

function countWords(text: string): number {
    return text.trim() ? text.trim().split(/\s+/).length : 0;
}

function getExportFilename(markdown: string, listName: string): string {
    const firstH1 = markdown.match(/^#\s+(.+)$/m);
    const title = firstH1 ? firstH1[1] : listName;
    return (
        title
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-|-$/g, "") + ".md"
    );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function getEditorMarkdown(editor: any): string {
    return editor?.storage?.markdown?.getMarkdown?.() ?? editor?.getText?.() ?? "";
}

export default function MarkdownEditor({
    listId,
    listName,
    initialContent,
    focusModeEnabled = false,
    onWordCountChange,
    onInsertText,
    onExportReady,
}: MarkdownEditorProps) {
    const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
    const [wordCount, setWordCount] = useState(0);
    const [isWriting, setIsWriting] = useState(false);
    const [toolbarPos, setToolbarPos] = useState<FloatingToolbarPos | null>(null);
    const [hasSelection, setHasSelection] = useState(false);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const writingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const latestContentRef = useRef(initialContent);
    const toolbarRef = useRef<HTMLDivElement>(null);

    const doSave = useCallback(
        async (content: string) => {
            setSaveStatus("saving");
            try {
                const firstH1 = content.match(/^#\s+(.+)$/m);
                const title = firstH1 ? firstH1[1].trim() : undefined;
                const words = countWords(
                    content.replace(/#{1,6}\s+/g, "").replace(/[_*`~]/g, "")
                );
                await draftsAPI.update(listId, { content, title, word_count: words });
                setSaveStatus("saved");
                setTimeout(() => setSaveStatus("idle"), 2500);
            } catch {
                setSaveStatus("error");
            }
        },
        [listId]
    );

    const editor = useEditor({
        immediatelyRender: false,
        extensions: [
            StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
            Markdown.configure({ html: false, transformPastedText: true }),
            Placeholder.configure({
                placeholder: "Start writing…",
            }),
            TypewriterScrolling.configure({ enabled: true, offset: 0.4 }),
            FocusMode.configure({ enabled: focusModeEnabled }),
        ],
        content: initialContent,
        editorProps: {
            attributes: { class: "writing-editor-inner", spellcheck: "true" },
        },
        onUpdate({ editor: ed }: { editor: Editor }) {
            const text = ed.getText();
            const words = countWords(text);
            setWordCount(words);
            onWordCountChange?.(words);
            latestContentRef.current = getEditorMarkdown(ed);

            setIsWriting(true);
            if (writingTimerRef.current) clearTimeout(writingTimerRef.current);
            writingTimerRef.current = setTimeout(() => setIsWriting(false), 3000);

            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => {
                doSave(latestContentRef.current);
            }, 5000);
        },
    });

    // Floating toolbar: track selection
    useEffect(() => {
        if (!editor) return;

        const updateToolbar = () => {
            const { from, to } = editor.state.selection;
            const hasText = from !== to;
            setHasSelection(hasText);

            if (!hasText) {
                setToolbarPos(null);
                return;
            }

            const domSel = window.getSelection();
            if (!domSel || domSel.rangeCount === 0) {
                setToolbarPos(null);
                return;
            }

            const range = domSel.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            if (!rect.width) {
                setToolbarPos(null);
                return;
            }

            const TOOLBAR_HEIGHT = 38;
            setToolbarPos({
                top: rect.top + window.scrollY - TOOLBAR_HEIGHT - 8,
                left: rect.left + window.scrollX + rect.width / 2,
            });
        };

        editor.on("selectionUpdate", updateToolbar);
        editor.on("blur", () => {
            setTimeout(() => {
                if (!editor.isFocused) {
                    setToolbarPos(null);
                    setHasSelection(false);
                }
            }, 150);
        });
        return () => {
            editor.off("selectionUpdate", updateToolbar);
        };
    }, [editor]);

    // Focus mode sync from external prop
    useEffect(() => {
        if (!editor) return;
        toggleFocusMode(editor, focusModeEnabled);
    }, [editor, focusModeEnabled]);

    // Initial word count
    useEffect(() => {
        if (!editor) return;
        const words = countWords(editor.getText());
        setWordCount(words);
        onWordCountChange?.(words);
    }, [editor, onWordCountChange]);

    // Insert-text handler for external callers
    useEffect(() => {
        if (!editor || !onInsertText) return;
        onInsertText((text: string) => {
            editor.chain().focus().insertContent(`\n\n> ${text}\n\n`).run();
        });
    }, [editor, onInsertText]);

    // Flush pending save on unmount
    useEffect(() => {
        return () => {
            if (debounceRef.current) {
                clearTimeout(debounceRef.current);
                doSave(latestContentRef.current);
            }
            if (writingTimerRef.current) clearTimeout(writingTimerRef.current);
        };
    }, [doSave]);

    // Register export handler with parent (for Navbar button)
    useEffect(() => {
        if (!editor || !onExportReady) return;
        onExportReady(() => {
            const markdown = getEditorMarkdown(editor);
            const filename = getExportFilename(markdown, listName);
            const blob = new Blob([markdown], { type: "text/markdown" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = filename;
            a.click();
            URL.revokeObjectURL(url);
        });
    }, [editor, onExportReady, listName]);

    const handleRetry = useCallback(() => {
        doSave(latestContentRef.current);
    }, [doSave]);

    if (!editor) return null;

    return (
        <div
            className={`writing-editor-wrapper${isWriting ? " writing-active" : ""}${focusModeEnabled ? " focus-mode-on" : ""}`}
        >
            {/* Floating bubble menu */}
            {toolbarPos && hasSelection && editor && (
                <div
                    ref={toolbarRef}
                    className="writing-bubble-menu"
                    style={{
                        position: "fixed",
                        top: toolbarPos.top,
                        left: toolbarPos.left,
                        transform: "translateX(-50%)",
                        zIndex: 100,
                    }}
                    onMouseDown={(e) => e.preventDefault()}
                >
                    <button
                        onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleBold().run(); }}
                        className={`bubble-btn${editor.isActive("bold") ? " active" : ""}`}
                        title="Bold"
                    >
                        <strong>B</strong>
                    </button>
                    <button
                        onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleItalic().run(); }}
                        className={`bubble-btn${editor.isActive("italic") ? " active" : ""}`}
                        title="Italic"
                    >
                        <em>I</em>
                    </button>
                    <button
                        onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleHeading({ level: 2 }).run(); }}
                        className={`bubble-btn${editor.isActive("heading", { level: 2 }) ? " active" : ""}`}
                        title="Heading 2"
                    >
                        H2
                    </button>
                    <button
                        onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleHeading({ level: 3 }).run(); }}
                        className={`bubble-btn${editor.isActive("heading", { level: 3 }) ? " active" : ""}`}
                        title="Heading 3"
                    >
                        H3
                    </button>
                    <div className="bubble-sep" />
                    <button
                        onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleBlockquote().run(); }}
                        className={`bubble-btn${editor.isActive("blockquote") ? " active" : ""}`}
                        title="Blockquote"
                    >
                        ❝
                    </button>
                    <button
                        onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleBulletList().run(); }}
                        className={`bubble-btn${editor.isActive("bulletList") ? " active" : ""}`}
                        title="Bullet list"
                    >
                        •—
                    </button>
                </div>
            )}

            {/* Scrolling editor area */}
            <div className="typewriter-scroll-container writing-scroll-area">
                <div className="writing-prose-column">
                    <EditorContent editor={editor} />
                </div>
            </div>

            <WritingStatusBar wordCount={wordCount} saveStatus={saveStatus} onRetry={handleRetry} />
        </div>
    );
}
