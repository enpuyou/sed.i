"use client";

import { useEditor, EditorContent } from "@tiptap/react";
import { Editor, Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { Markdown } from "tiptap-markdown";
import { useEffect, useRef, useCallback, useState } from "react";
import { TypewriterScrolling } from "@/components/editor/TypewriterScrolling";
import WritingStatusBar from "@/components/WritingStatusBar";
import ThemeToggle from "@/components/ThemeToggle";
import { draftsAPI } from "@/lib/api";

// Known slash commands
const SLASH_COMMANDS = ["toc"];

// Tiptap extension factory: detects slash-command context and notifies via callback
function makeSlashCommandHint(onSlashMode: (active: boolean) => void) {
  return Extension.create({
    name: "slashCommandHint",
    addProseMirrorPlugins() {
      let lastSlashMode = false;
      return [
        new Plugin({
          key: new PluginKey("slashCommandHint"),
          view() {
            return {
              update(view) {
                const { state } = view;
                const { $from } = state.selection;
                const lineStart = $from.start();
                const lineText = state.doc.textBetween(lineStart, $from.pos);

                const slashMatch = lineText.match(/(?:^|\s)(\/\S*)$/);
                let inSlashMode = false;
                if (slashMatch) {
                  const typed = slashMatch[1].slice(1).toLowerCase();
                  inSlashMode =
                    typed === "" ||
                    SLASH_COMMANDS.some((cmd) => cmd.startsWith(typed));
                }

                // Update DOM attr for caret color CSS
                const wrapper = (view.dom as HTMLElement).closest(
                  ".writing-editor-wrapper",
                ) as HTMLElement | null;
                const target = wrapper ?? (view.dom as HTMLElement);
                if (inSlashMode) {
                  target.setAttribute("data-slash-mode", "true");
                } else {
                  target.removeAttribute("data-slash-mode");
                }

                // Notify React only on change to avoid thrashing
                if (inSlashMode !== lastSlashMode) {
                  lastSlashMode = inSlashMode;
                  onSlashMode(inSlashMode);
                }
              },
            };
          },
        }),
      ];
    },
  });
}

// Decoration plugin: scans document on every state change and adds
// .toc-heading / .toc-list classes to the h3 + ul TOC nodes reactively.
// This survives ProseMirror DOM re-renders because decorations are re-applied each update.
const tocDecorationKey = new PluginKey("tocDecoration");
const TocDecoration = Extension.create({
  name: "tocDecoration",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: tocDecorationKey,
        state: {
          init(_, { doc }) {
            return buildTocDecorations(doc);
          },
          apply(tr, old) {
            return tr.docChanged ? buildTocDecorations(tr.doc) : old;
          },
        },
        props: {
          decorations(state) {
            return tocDecorationKey.getState(state);
          },
        },
      }),
    ];
  },
});

function buildTocDecorations(
  doc: import("@tiptap/pm/model").Node,
): DecorationSet {
  const decorations: Decoration[] = [];
  doc.forEach((node, offset) => {
    if (
      node.type.name === "heading" &&
      node.attrs.level === 3 &&
      node.textContent === "Table of Contents"
    ) {
      decorations.push(
        Decoration.node(offset, offset + node.nodeSize, {
          class: "toc-heading",
        }),
      );
      // Check next sibling
      const nextOffset = offset + node.nodeSize;
      const next = doc.nodeAt(nextOffset);
      if (next && next.type.name === "bulletList") {
        decorations.push(
          Decoration.node(nextOffset, nextOffset + next.nodeSize, {
            class: "toc-list",
          }),
        );
      }
    }
  });
  return DecorationSet.create(doc, decorations);
}

type SaveStatus = "idle" | "saving" | "saved" | "error";

interface MarkdownEditorProps {
  listId: string;
  listName: string;
  initialContent: string;
  initialFullscreen?: boolean;
  onWordCountChange?: (count: number) => void;
  onInsertText?: (handler: (text: string) => void) => void;
  onExportReady?: (
    fn: (format?: "md" | "pdf" | "docx") => void | Promise<void>,
  ) => void;
  onExport?: (format: "md" | "pdf" | "docx") => void;
  onFullscreenChange?: (fs: boolean) => void;
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
  return (
    editor?.storage?.markdown?.getMarkdown?.() ?? editor?.getText?.() ?? ""
  );
}

export default function MarkdownEditor({
  listId,
  listName,
  initialContent,
  initialFullscreen = false,
  onWordCountChange,
  onInsertText,
  onExportReady,
  onExport,
  onFullscreenChange,
}: MarkdownEditorProps) {
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [wordCount, setWordCount] = useState(0);
  const [isWriting, setIsWriting] = useState(false);
  const [_hasSelection, setHasSelection] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(initialFullscreen);
  const [exportOpen, setExportOpen] = useState(false);
  const [slashMode, setSlashMode] = useState(false);
  const slashModeCallbackRef = useRef<(active: boolean) => void>((active) =>
    setSlashMode(active),
  );
  const exportDropdownRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const writingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savedStatusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const latestContentRef = useRef(initialContent);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const doSave = useCallback(
    async (content: string) => {
      setSaveStatus("saving");
      try {
        const firstH1 = content.match(/^#\s+(.+)$/m);
        const title = firstH1 ? firstH1[1].trim() : undefined;
        const words = countWords(
          content.replace(/#{1,6}\s+/g, "").replace(/[_*`~]/g, ""),
        );
        await draftsAPI.update(listId, { content, title, word_count: words });
        setSaveStatus("saved");
        if (savedStatusTimerRef.current) {
          clearTimeout(savedStatusTimerRef.current);
        }
        savedStatusTimerRef.current = setTimeout(() => {
          setSaveStatus("idle");
          savedStatusTimerRef.current = null;
        }, 2500);
      } catch {
        setSaveStatus("error");
      }
    },
    [listId],
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
      makeSlashCommandHint((active) => slashModeCallbackRef.current(active)),
      TocDecoration,
    ],
    content: initialContent,
    editorProps: {
      attributes: { class: "writing-editor-inner", spellcheck: "true" },
      handleKeyDown(view, event) {
        if (event.key !== "Enter") return false;
        const { state } = view;
        const { $from } = state.selection;
        const lineStart = $from.start();
        const lineText = state.doc.textBetween(lineStart, $from.pos);
        if (!lineText.trim().endsWith("/toc")) return false;

        event.preventDefault();
        const allHeadings: { level: number; text: string }[] = [];
        state.doc.descendants((node) => {
          if (node.type.name === "heading") {
            allHeadings.push({
              level: node.attrs.level,
              text: node.textContent,
            });
          }
        });

        // Exclude only the first h1 (article title) and "Table of Contents" heading itself
        const TOC_LABEL = "Table of Contents";
        let firstH1Seen = false;
        const headings = allHeadings.filter((h) => {
          if (h.text === TOC_LABEL) return false;
          if (h.level === 1 && !firstH1Seen) {
            firstH1Seen = true;
            return false;
          }
          return true;
        });

        if (headings.length === 0) {
          // Just delete the /toc text and leave a plain message
          view.dispatch(state.tr.delete(lineStart, $from.pos));
          view.dispatch(view.state.tr.insertText("(No headings found)"));
          return true;
        }

        // Delete the entire paragraph containing "/toc" and replace with TOC nodes.
        // $from.before(1) = start of the top-level node (the paragraph), nodeSize wraps it.
        const paraStart = $from.before($from.depth);
        const paraEnd = $from.after($from.depth);

        const s = view.state.schema;
        const baseLevel = Math.min(...headings.map((h) => h.level));

        // Build proper nested lists so indentation survives markdown serialization/parsing.
        // Each heading becomes a listItem; deeper levels are nested bulletLists inside
        // their nearest shallower parent's listItem.
        function buildNestedList(
          hs: typeof headings,
        ): ReturnType<typeof s.nodes.bulletList.create> {
          const items: ReturnType<typeof s.nodes.listItem.create>[] = [];
          let i = 0;
          while (i < hs.length) {
            const h = hs[i];
            const depth = h.level - baseLevel; // 0 = top level
            if (depth > 0) {
              i++;
              continue;
            } // skip — will be nested below

            // Collect consecutive children (level > h.level) for nesting
            const children: typeof headings = [];
            let j = i + 1;
            while (j < hs.length && hs[j].level > h.level) {
              children.push(hs[j]);
              j++;
            }

            const para = s.nodes.paragraph.create({}, [s.text(h.text)]);
            if (children.length > 0) {
              // Remap children so their baseLevel is h.level+1
              const childBase = Math.min(...children.map((c) => c.level));
              function buildChildren(
                chs: typeof headings,
                cb: number,
              ): ReturnType<typeof s.nodes.bulletList.create> {
                const citems: ReturnType<typeof s.nodes.listItem.create>[] = [];
                let ci = 0;
                while (ci < chs.length) {
                  const ch = chs[ci];
                  if (ch.level - cb > 0) {
                    ci++;
                    continue;
                  }
                  const grandchildren: typeof headings = [];
                  let gj = ci + 1;
                  while (gj < chs.length && chs[gj].level > ch.level) {
                    grandchildren.push(chs[gj]);
                    gj++;
                  }
                  const cp = s.nodes.paragraph.create({}, [s.text(ch.text)]);
                  if (grandchildren.length > 0) {
                    const gcBase = Math.min(
                      ...grandchildren.map((g) => g.level),
                    );
                    citems.push(
                      s.nodes.listItem.create({}, [
                        cp,
                        buildChildren(grandchildren, gcBase),
                      ]),
                    );
                  } else {
                    citems.push(s.nodes.listItem.create({}, [cp]));
                  }
                  ci = gj;
                }
                return s.nodes.bulletList.create({}, citems);
              }
              items.push(
                s.nodes.listItem.create({}, [
                  para,
                  buildChildren(children, childBase),
                ]),
              );
            } else {
              items.push(s.nodes.listItem.create({}, [para]));
            }
            i = j;
          }
          return s.nodes.bulletList.create({}, items);
        }

        const nodes = [
          s.nodes.heading.create({ level: 3 }, s.text(TOC_LABEL)),
          buildNestedList(headings),
        ];
        // Single transaction: replace the /toc paragraph with the TOC block
        view.dispatch(state.tr.replaceWith(paraStart, paraEnd, nodes));
        return true;
      },
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
      }, 1500);
    },
  });

  // Floating toolbar: position via ref mutation (no setState → no reflow/wobble)
  useEffect(() => {
    if (!editor) return;

    const updateToolbar = () => {
      const { from, to } = editor.state.selection;
      const hasText = from !== to;

      if (!hasText) {
        setHasSelection(false);
        if (toolbarRef.current) toolbarRef.current.style.visibility = "hidden";
        return;
      }

      const domSel = window.getSelection();
      if (!domSel || domSel.rangeCount === 0) {
        setHasSelection(false);
        if (toolbarRef.current) toolbarRef.current.style.visibility = "hidden";
        return;
      }

      const range = domSel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      if (!rect.width) {
        setHasSelection(false);
        if (toolbarRef.current) toolbarRef.current.style.visibility = "hidden";
        return;
      }

      const TOOLBAR_HEIGHT = 38;
      if (toolbarRef.current) {
        toolbarRef.current.style.top = `${rect.top - TOOLBAR_HEIGHT - 8}px`;
        toolbarRef.current.style.left = `${rect.left + rect.width / 2}px`;
        toolbarRef.current.style.visibility = "visible";
      }
      setHasSelection(true);
    };

    editor.on("selectionUpdate", updateToolbar);
    editor.on("blur", () => {
      setTimeout(() => {
        if (!editor.isFocused) {
          setHasSelection(false);
          if (toolbarRef.current)
            toolbarRef.current.style.visibility = "hidden";
        }
      }, 150);
    });
    return () => {
      editor.off("selectionUpdate", updateToolbar);
    };
  }, [editor]);

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

  // Cmd+S / Ctrl+S — immediate save
  useEffect(() => {
    if (!editor) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s" && editor.isFocused) {
        e.preventDefault();
        if (debounceRef.current) clearTimeout(debounceRef.current);
        doSave(latestContentRef.current);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [editor, doSave]);

  // Flush pending save on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        doSave(latestContentRef.current);
      }
      if (writingTimerRef.current) clearTimeout(writingTimerRef.current);
      if (savedStatusTimerRef.current)
        clearTimeout(savedStatusTimerRef.current);
    };
  }, [doSave]);

  // Register export handler with parent (for Navbar button)
  useEffect(() => {
    if (!editor || !onExportReady) return;
    onExportReady(async (format: "md" | "pdf" | "docx" = "md") => {
      const markdown = getEditorMarkdown(editor);
      const baseName = getExportFilename(markdown, listName).replace(
        /\.md$/,
        "",
      );

      if (format === "md") {
        const blob = new Blob([markdown], { type: "text/markdown" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${baseName}.md`;
        a.click();
        URL.revokeObjectURL(url);
      } else if (format === "pdf") {
        // Print the rendered editor HTML (not raw markdown)
        const editorEl = document.querySelector(".writing-editor-inner");
        const html = editorEl
          ? editorEl.innerHTML
          : `<pre>${markdown.replace(/</g, "&lt;")}</pre>`;
        const printWin = window.open("", "_blank");
        if (printWin) {
          printWin.document
            .write(`<!DOCTYPE html><html><head><title>${baseName}</title><style>
                        body{font-family:Georgia,serif;max-width:680px;margin:48px auto;line-height:1.8;font-size:16px;color:#1a1a1a}
                        h1{font-size:2em;margin:1.5em 0 0.5em}h2{font-size:1.5em;margin:1.25em 0 0.4em}h3{font-size:1.2em;margin:1em 0 0.3em}
                        p{margin:0 0 1em}blockquote{border-left:3px solid #ccc;margin:1em 0;padding-left:1em;color:#555}
                        ul,ol{margin:0 0 1em;padding-left:1.5em}li{margin:0.25em 0}
                        code{font-family:monospace;background:#f5f5f5;padding:0.1em 0.3em}
                        strong{font-weight:700}em{font-style:italic}
                        @media print{body{margin:0}}</style></head><body>${html}</body></html>`);
          printWin.document.close();
          printWin.print();
        }
      } else if (format === "docx") {
        const { Document, Paragraph, TextRun, HeadingLevel, Packer } =
          await import("docx");
        const lines = markdown.split("\n");
        const children: InstanceType<typeof Paragraph>[] = [];
        for (const line of lines) {
          if (/^# /.test(line)) {
            children.push(
              new Paragraph({
                text: line.slice(2),
                heading: HeadingLevel.HEADING_1,
              }),
            );
          } else if (/^## /.test(line)) {
            children.push(
              new Paragraph({
                text: line.slice(3),
                heading: HeadingLevel.HEADING_2,
              }),
            );
          } else if (/^### /.test(line)) {
            children.push(
              new Paragraph({
                text: line.slice(4),
                heading: HeadingLevel.HEADING_3,
              }),
            );
          } else {
            children.push(new Paragraph({ children: [new TextRun(line)] }));
          }
        }
        const doc = new Document({ sections: [{ children }] });
        const buf = await Packer.toBlob(doc);
        const url = URL.createObjectURL(buf);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${baseName}.docx`;
        a.click();
        URL.revokeObjectURL(url);
      }
    });
  }, [editor, onExportReady, listName]);

  const handleRetry = useCallback(() => {
    doSave(latestContentRef.current);
  }, [doSave]);

  // Close export dropdown on outside click
  useEffect(() => {
    if (!exportOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        exportDropdownRef.current &&
        !exportDropdownRef.current.contains(e.target as Node)
      ) {
        setExportOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [exportOpen]);

  if (!editor) return null;

  return (
    <div
      className={`writing-editor-wrapper${isWriting ? " writing-active" : ""}${isFullscreen ? " fullscreen-mode-on" : ""}`}
    >
      {/* Top-right chrome: ThemeToggle + Export — only in fullscreen mode */}
      {isFullscreen && onExport && (
        <div className="fullscreen-top-bar">
          <ThemeToggle />
          <div ref={exportDropdownRef} className="relative flex items-center">
            <button
              onClick={() => setExportOpen((v) => !v)}
              className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors"
              style={{ color: "var(--color-text-primary)" }}
              title="Export"
            >
              Export ▾
            </button>
            {exportOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 bg-[var(--color-bg-primary)] border border-[var(--color-border)] min-w-[110px]">
                {(["md", "pdf", "docx"] as const).map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => {
                      setExportOpen(false);
                      onExport(fmt);
                    }}
                    className="block w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-accent)] transition-colors"
                    style={{ color: "var(--color-text-primary)" }}
                  >
                    .{fmt}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Floating bubble menu — always mounted, positioned via ref to avoid reflow */}
      {editor && (
        <div
          ref={toolbarRef}
          className="writing-bubble-menu"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            transform: "translateX(-50%)",
            zIndex: 100,
            visibility: "hidden",
          }}
          onMouseDown={(e) => e.preventDefault()}
        >
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              editor.chain().focus().toggleBold().run();
            }}
            className={`bubble-btn${editor.isActive("bold") ? " active" : ""}`}
            title="Bold"
          >
            <strong>B</strong>
          </button>
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              editor.chain().focus().toggleItalic().run();
            }}
            className={`bubble-btn${editor.isActive("italic") ? " active" : ""}`}
            title="Italic"
          >
            <em>I</em>
          </button>
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              editor.chain().focus().toggleHeading({ level: 2 }).run();
            }}
            className={`bubble-btn${editor.isActive("heading", { level: 2 }) ? " active" : ""}`}
            title="Heading 2"
          >
            H2
          </button>
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              editor.chain().focus().toggleHeading({ level: 3 }).run();
            }}
            className={`bubble-btn${editor.isActive("heading", { level: 3 }) ? " active" : ""}`}
            title="Heading 3"
          >
            H3
          </button>
          <div className="bubble-sep" />
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              editor.chain().focus().toggleBlockquote().run();
            }}
            className={`bubble-btn${editor.isActive("blockquote") ? " active" : ""}`}
            title="Blockquote"
          >
            ❝
          </button>
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              editor.chain().focus().toggleBulletList().run();
            }}
            className={`bubble-btn${editor.isActive("bulletList") ? " active" : ""}`}
            title="Bullet list"
          >
            •—
          </button>
        </div>
      )}

      {/* Scrolling editor area */}
      <div
        ref={scrollContainerRef}
        className="typewriter-scroll-container writing-scroll-area"
      >
        <div className="writing-prose-column">
          <EditorContent editor={editor} />
        </div>
      </div>

      {/* Help panel */}
      {showHelp && (
        <div className="editor-help-panel">
          <div className="editor-help-section">
            <div className="editor-help-label">Formatting</div>
            <div className="editor-help-row">
              <kbd>#</kbd>
              <span>Heading 1</span>
            </div>
            <div className="editor-help-row">
              <kbd>##</kbd>
              <span>Heading 2</span>
            </div>
            <div className="editor-help-row">
              <kbd>**text**</kbd>
              <span>Bold</span>
            </div>
            <div className="editor-help-row">
              <kbd>_text_</kbd>
              <span>Italic</span>
            </div>
            <div className="editor-help-row">
              <kbd>- item</kbd>
              <span>Bullet list</span>
            </div>
            <div className="editor-help-row">
              <kbd>&gt; text</kbd>
              <span>Blockquote</span>
            </div>
            <div className="editor-help-row">
              <kbd>`code`</kbd>
              <span>Inline code</span>
            </div>
          </div>
          <div className="editor-help-section">
            <div className="editor-help-label">Commands</div>
            <div className="editor-help-row">
              <kbd>/toc</kbd>
              <span>Insert table of contents</span>
            </div>
          </div>
          <div className="editor-help-section">
            <div className="editor-help-label">Shortcuts</div>
            <div className="editor-help-row">
              <kbd>Ctrl B</kbd>
              <span>Bold</span>
            </div>
            <div className="editor-help-row">
              <kbd>Ctrl I</kbd>
              <span>Italic</span>
            </div>
            <div className="editor-help-row">
              <kbd>Esc</kbd>
              <span>Exit writing mode</span>
            </div>
          </div>
        </div>
      )}

      <div className="editor-chrome-bar">
        <WritingStatusBar
          wordCount={wordCount}
          saveStatus={saveStatus}
          onRetry={handleRetry}
          className="editor-status-inline"
          slashMode={slashMode}
        />
        <div className="editor-chrome-actions">
          <button
            className="editor-chrome-btn"
            onClick={() => setShowHelp((v) => !v)}
            title="Show commands & shortcuts"
            aria-label="Help"
          >
            {showHelp ? (
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              >
                <line x1="2" y1="2" x2="10" y2="10" />
                <line x1="10" y1="2" x2="2" y2="10" />
              </svg>
            ) : (
              <svg
                width="13"
                height="13"
                viewBox="0 0 13 13"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4.5 4.5C4.5 3.4 5.3 2.5 6.5 2.5s2 .9 2 2c0 1.2-2 1.8-2 3" />
                <circle
                  cx="6.5"
                  cy="10"
                  r="0.6"
                  fill="currentColor"
                  stroke="none"
                />
              </svg>
            )}
          </button>
          <button
            className="editor-chrome-btn"
            onClick={() => {
              const next = !isFullscreen;
              setIsFullscreen(next);
              onFullscreenChange?.(next);
            }}
            title={isFullscreen ? "Exit fullscreen" : "Fullscreen editor"}
            aria-label={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          >
            {isFullscreen ? (
              <svg
                width="14"
                height="14"
                viewBox="0 0 14 14"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 1h4v4M5 1H1v4M9 13h4V9M5 13H1V9" />
              </svg>
            ) : (
              <svg
                width="14"
                height="14"
                viewBox="0 0 14 14"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M1 5V1h4M13 5V1H9M1 9v4h4M13 9v4H9" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
