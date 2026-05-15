"use client";

import React, { useRef, useEffect, useState } from "react";
import { addHeadingAnchors } from "@/lib/bionicReading";
import { useReadingSettings } from "@/contexts/ReadingSettingsContext";

interface Highlight {
  id: string;
  text: string;
  start_offset: number;
  end_offset: number;
  color: string;
  note?: string;
}

interface HighlightRendererProps {
  html: string;
  highlights: Highlight[];
  onHighlightClick?: (
    highlight: Highlight,
    clickedElement?: HTMLElement,
  ) => void;
  onImageClick?: (src: string) => void;
}

/**
 * Helper to wrap start of words in strong tags for Bionic Reading
 */
const applyBionicInfoToText = (text: string): string => {
  return text
    .split(/(\s+)/)
    .map((part) => {
      if (/^\s+$/.test(part) || part.length <= 1) return part;
      const splitIndex = Math.ceil(part.length * 0.4);
      return `<strong>${part.slice(0, splitIndex)}</strong>${part.slice(splitIndex)}`;
    })
    .join("");
};

import parse, {
  DOMNode,
  Element,
  domToReact,
  attributesToProps,
} from "html-react-parser";
import InlineHighlight from "./InlineHighlight";
import { stripDocumentWrappers } from "@/lib/bionicReading";

const HighlightRenderer = ({
  html,
  highlights,
  onHighlightClick,
  onImageClick,
  onDeleteHighlight,
  onUpdateHighlight,
  newlyCreatedHighlightId, // to trigger auto-open
  onShowConnections,
  connectedHighlightIds = new Set(),
}: HighlightRendererProps & {
  onDeleteHighlight?: (id: string) => void;
  onUpdateHighlight?: () => void;
  newlyCreatedHighlightId?: string | null;
  onShowConnections?: (highlightId: string) => void;
  connectedHighlightIds?: Set<string>;
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  // Strip PDF document wrappers (DOCTYPE, html, head, body) to avoid React hydration errors
  // No-op for regular articles (trafilatura doesn't include these wrappers)
  const [renderedHtml, setRenderedHtml] = useState<string>(
    stripDocumentWrappers(html),
  );
  const { settings } = useReadingSettings();
  const [isMobile, setIsMobile] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Removed: formattingTimestamp effect was too aggressive
  // Formatting operations already handle ephemeral elements properly
  // and don't need to force-close all note editors globally

  // Detect mobile
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 640);
    };
    checkMobile(); // Check on mount
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  useEffect(() => {
    // ... (Existing logic to process HTML with bionic reading and segments) ...
    // BUT instead of manipulating DOM directly and replacing nodes,
    // we should process the HTML string fully first, then parse it.

    // Actually, the existing logic builds an HTML string via DOM manipulation on fragments
    // but ultimately returns a String or modified DOM to dangerouslySetInnerHTML.

    // The previous implementation utilized `originalDoc` and `textNodes` to inject spans into a DOM structure,
    // then applied heading anchors, and finally set `renderedHtml` as a string.

    // We can keep the logic "as is" to generate the string with <span data-highlight-id="...">
    // and THEN use html-react-parser on THAT string.

    const activeHighlights = [...highlights];
    const parser = new DOMParser();

    // Always parse from the stripped source html — same content as the initial renderedHtml
    // state, but doesn't create a setState → re-run → setState loop.
    // stripDocumentWrappers handles PDF wrappers (DOCTYPE/html/head/body) and is a no-op
    // for normal articles, so offsets are consistent with what the reader renders.
    const originalDoc = parser.parseFromString(
      stripDocumentWrappers(html),
      "text/html",
    );

    const walker = document.createTreeWalker(
      originalDoc.body,
      NodeFilter.SHOW_TEXT,
      null,
    );

    let charIndex = 0;
    const textNodes: Array<{ node: Text; startChar: number; endChar: number }> =
      [];

    let textNode: Text | null;
    while ((textNode = walker.nextNode() as Text | null)) {
      const nodeText = textNode.textContent || "";
      if (nodeText.length === 0) continue;

      textNodes.push({
        node: textNode,
        startChar: charIndex,
        endChar: charIndex + nodeText.length,
      });
      charIndex += nodeText.length;
    }

    // Define Segment interface first
    interface Segment {
      start: number;
      end: number;
      highlight?: Highlight;
      isFirst?: boolean;
      isLast?: boolean;
    }

    // Start with collecting operations instead of mutating immediately
    type Operation = {
      node: Text;
      segments: Array<Segment>;
    };

    const operations: Operation[] = [];
    // Map to track all segments for a specific highlight ID across multiple text nodes
    const highlightSegmentsMap: Record<string, Segment[]> = {};

    textNodes.forEach(({ node, startChar, endChar }) => {
      const parent = node.parentNode;
      if (!parent) return;

      const nodeText = node.textContent || "";
      const overlappingHighlights = activeHighlights
        .filter((h) => h.start_offset < endChar && h.end_offset > startChar)
        .sort((a, b) => a.start_offset - b.start_offset);

      const segments: Segment[] = [];
      let currentPos = 0;

      if (overlappingHighlights.length > 0) {
        overlappingHighlights.forEach((highlight) => {
          const highlightStart = Math.max(
            0,
            highlight.start_offset - startChar,
          );
          const highlightEnd = Math.min(
            nodeText.length,
            highlight.end_offset - startChar,
          );

          if (highlightEnd <= currentPos) return;

          if (currentPos < highlightStart) {
            segments.push({ start: currentPos, end: highlightStart });
            currentPos = highlightStart;
          }

          const segmentStart = Math.max(currentPos, highlightStart);
          const segmentEnd = highlightEnd;

          if (segmentStart < segmentEnd) {
            const segment: Segment = {
              start: segmentStart,
              end: segmentEnd,
              highlight,
              isLast: false, // Default to false, will solve in post-process
            };
            segments.push(segment);

            // Track for post-processing
            if (!highlightSegmentsMap[highlight.id]) {
              highlightSegmentsMap[highlight.id] = [];
            }
            highlightSegmentsMap[highlight.id].push(segment);

            currentPos = segmentEnd;
          }
        });
      }

      if (currentPos < nodeText.length) {
        segments.push({ start: currentPos, end: nodeText.length });
      }

      // Record operation to be performed later
      if (segments.length > 0) {
        operations.push({ node, segments });
      }
    });

    // POST-PROCESS: Mark the first and last segment for each highlight
    Object.values(highlightSegmentsMap).forEach((segments) => {
      if (segments.length > 0) {
        segments[0].isFirst = true;
        segments[segments.length - 1].isLast = true;
      }
    });

    // APPLY OPERATIONS
    operations.forEach(({ node, segments }) => {
      const parent = node.parentNode;
      if (!parent) return; // Should verify parent still exists (it should)

      const nodeText = node.textContent || "";
      const fragment = document.createDocumentFragment();

      segments.forEach((segment) => {
        const rawText = nodeText.substring(segment.start, segment.end);
        // Apply Bionic Reading transformation if enabled
        const contentHtml = settings.bionicReading
          ? applyBionicInfoToText(rawText)
          : rawText;

        if (segment.highlight) {
          const span = document.createElement("span");
          span.dataset.highlightId = segment.highlight.id;
          span.dataset.highlightColor = segment.highlight.color;
          span.dataset.highlightNote = segment.highlight.note || "";

          if (segment.isFirst) {
            span.dataset.highlightIsFirst = "true";
          }
          if (segment.isLast) {
            span.dataset.highlightIsLast = "true";
          }

          span.innerHTML = contentHtml;
          fragment.appendChild(span);
        } else {
          if (settings.bionicReading) {
            const span = document.createElement("span");
            span.innerHTML = contentHtml;
            fragment.appendChild(span);
          } else {
            fragment.appendChild(document.createTextNode(rawText));
          }
        }
      });

      parent.replaceChild(fragment, node);
    });

    // Apply heading anchors to the final processed HTML
    setRenderedHtml(addHeadingAnchors(originalDoc.body.innerHTML));
  }, [html, highlights, settings.bionicReading]);

  // ... (transform function)
  // const [editingId, setEditingId] = useState<string | null>(null); // REMOVED DUPLICATE
  const [draftNote, setDraftNote] = useState<string>("");

  // Auto-open newly created highlight
  useEffect(() => {
    if (newlyCreatedHighlightId) {
      setEditingId(newlyCreatedHighlightId);
      setDraftNote(""); // New highlights have no note initially
    }
  }, [newlyCreatedHighlightId]);

  // Sync draft note when opening an existing highlight
  const handleToggleHighlight = (id: string, isOpen: boolean) => {
    if (isOpen) {
      const highlight = highlights.find((h) => h.id === id);
      setEditingId(id);
      setDraftNote(highlight?.note || "");
    } else {
      setEditingId(null);
      setDraftNote("");
    }
  };

  // ... (transform function)
  const transform = (node: DOMNode, index: number) => {
    // Sanitize attribute names globally to prevent React "Invalid attribute name" crashes
    // This happens when parsed HTML has unescaped quotes causing htmlparser2 to create malformed attribute keys
    if (node instanceof Element && node.attribs) {
      const validAttribs: Record<string, string> = {};
      for (const key in node.attribs) {
        if (/^[a-zA-Z0-9_\-:]+$/.test(key)) {
          validAttribs[key] = node.attribs[key];
        }
      }
      node.attribs = validAttribs;
    }

    if (
      node instanceof Element &&
      node.name === "span" &&
      node.attribs["data-highlight-id"]
    ) {
      const id = node.attribs["data-highlight-id"];
      const color = node.attribs["data-highlight-color"];
      const note = node.attribs["data-highlight-note"];
      const isFirst = node.attribs["data-highlight-is-first"] === "true";
      const isLast = node.attribs["data-highlight-is-last"] === "true";

      const highlight = highlights.find((h) => h.id === id);
      // Fallback if highlight not found in props (should match)
      const currentNote = highlight ? highlight.note : note;

      const isOpen = editingId === id;

      return (
        <InlineHighlight
          key={`${id}-${index}`}
          id={id}
          color={color}
          initialNote={currentNote}
          isOpen={isOpen && isLast}
          onToggle={(open) => handleToggleHighlight(id, open)}
          draftNote={isOpen ? draftNote : undefined}
          onNoteChange={setDraftNote}
          showIndicators={isLast}
          showConnectionIndicator={isFirst}
          onDelete={onDeleteHighlight}
          onUpdate={onUpdateHighlight}
          onHighlightClick={
            onHighlightClick
              ? (_id, element) =>
                  onHighlightClick(
                    highlight || {
                      id,
                      color,
                      text: "",
                      start_offset: 0,
                      end_offset: 0,
                    },
                    element as HTMLElement,
                  )
              : undefined
          }
          onShowConnections={onShowConnections}
          hasConnections={connectedHighlightIds.has(id)}
          isMobile={isMobile}
        >
          {domToReact(node.children as DOMNode[], { replace: transform })}
        </InlineHighlight>
      );
    }
    // ...

    // Handle Images
    if (node instanceof Element && node.name === "img" && onImageClick) {
      const src = node.attribs.src;
      if (src) {
        const props = attributesToProps(node.attribs);
        // Return props but add onClick
        // Ensure alt is a string (props.alt could be boolean or undefined)
        const altText =
          typeof props.alt === "string" ? props.alt : "Article image";
        return (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            {...props}
            alt={altText}
            onClick={(e) => {
              e.preventDefault();
              onImageClick(src);
            }}
            className={`cursor-zoom-in ${props.className || ""}`}
          />
        );
      }
    }
  };

  return (
    <div
      id="article-content"
      ref={containerRef}
      className={`cursor-text select-text ${settings.bionicReading ? "" : ""}`} // Removed [&_img] since handled in parser
    >
      {parse(renderedHtml, { replace: transform })}
    </div>
  );
};

export default React.memo(HighlightRenderer);
