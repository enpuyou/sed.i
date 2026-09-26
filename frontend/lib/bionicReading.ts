import DOMPurify from "dompurify";

/**
 * Sanitize article HTML before rendering. content.full_text is extracted from
 * arbitrary third-party pages the user doesn't control (trafilatura/PDF
 * extraction on the backend, the extension content script) and rendered via
 * html-react-parser — which converts HTML attributes (including event
 * handlers, javascript: URLs) into React props rather than stripping them
 * the way a sanitizer would. Call this on the final HTML string right before
 * it's parsed/rendered, after any DOM-manipulation steps (highlight-span
 * injection, addHeadingAnchors) — their data attributes, id, href, class,
 * and aria-label all survive DOMPurify's default ALLOWED_ATTR; only
 * dangerous vectors (event handlers, javascript: URLs, srcdoc, inline
 * script tags, etc.) are stripped.
 */
export function sanitizeArticleHtml(html: string): string {
  if (typeof window === "undefined") return html; // SSR safety — DOMPurify needs a DOM
  return DOMPurify.sanitize(html);
}

/**
 * Convert text to bionic reading format.
 * Bolds the first ~50% of each word to create fixation points for faster reading.
 *
 * Example: "The quick brown fox" becomes "Th**e qu**ick br**own f**ox"
 * (where ** represents bold tags)
 */
export function toBionic(html: string): string {
  // Only process text nodes, not HTML tags
  return html.replace(/>([^<]+)</g, (_match, text) => {
    const bionicText = text.replace(/\b(\w+)\b/g, (word: string) => {
      if (word.length <= 1) return word;
      const boldLen = Math.ceil(word.length * 0.5);
      return `<strong>${word.slice(0, boldLen)}</strong>${word.slice(boldLen)}`;
    });
    return `>${bionicText}<`;
  });
}

/**
 * Add anchor IDs and hover links to headings in HTML.
 * Converts:
 *   <h2>My Section Title</h2>
 * To:
 *   <h2 id="my-section-title">
 *     <a href="#my-section-title" class="heading-anchor">#</a>
 *     My Section Title
 *   </h2>
 */
export function addHeadingAnchors(html: string): string {
  if (typeof document === "undefined") return html; // SSR safety

  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");

  // Track seen slugs to deduplicate — must match the logic in Reader.tsx TOC extraction
  const seenIds = new Map<string, number>();

  doc.querySelectorAll("h1, h2, h3, h4").forEach((heading) => {
    const text = heading.textContent || "";

    // Preserve existing id if present (matches TOC extractor behaviour).
    // Only generate a slug if there's no id already.
    let id = heading.id;
    if (!id && text) {
      id =
        text
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "") || "";
    }

    if (!id) return; // Skip if no id and no text content

    // Deduplicate: first occurrence keeps id, subsequent get -2, -3, ...
    const count = seenIds.get(id) ?? 0;
    seenIds.set(id, count + 1);
    const uniqueId = count === 0 ? id : `${id}-${count + 1}`;

    heading.id = uniqueId;

    // Create anchor link
    const anchor = doc.createElement("a");
    anchor.href = `#${uniqueId}`;
    anchor.className = "heading-anchor";
    anchor.textContent = ""; // Use CSS ::before for content to avoid affecting text offsets
    anchor.setAttribute("aria-label", `Link to ${heading.textContent}`);

    // Prepend anchor to heading
    heading.prepend(anchor);
  });

  return doc.body.innerHTML;
}

/**
 * Strip full document HTML wrappers (<!DOCTYPE>, <html>, <head>, <body> tags).
 * PDF extraction includes these for standalone HTML files, but they cause
 * React hydration errors when rendered inside Next.js components.
 *
 * Converts:
 *   <!DOCTYPE html><html><head>...</head><body><p>Content</p></body></html>
 * To:
 *   <p>Content</p>
 */
export function stripDocumentWrappers(html: string): string {
  // Remove DOCTYPE
  let content = html.replace(/<!DOCTYPE[^>]*>/i, "");

  // Parse and extract body content
  if (typeof document !== "undefined") {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(content, "text/html");
      return doc.body.innerHTML;
    } catch (e) {
      // If parsing fails, try regex fallback
      console.warn("DOMParser failed, using regex fallback:", e);
    }
  }

  // Regex fallback: extract content between <body> tags
  const bodyMatch = content.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  if (bodyMatch) {
    return bodyMatch[1];
  }

  // If no body tag, remove <html>, <head> tags but keep content
  content = content.replace(/<\/?html[^>]*>/gi, "");
  content = content.replace(/<head[^>]*>[\s\S]*?<\/head>/gi, "");

  return content;
}

/**
 * Sanitize HTML content before saving.
 * Removes:
 * 1. Heading anchors (# links)
 * 2. Ephemeral UI elements (highlight editors, indicators)
 */
export function sanitizeContentHtml(html: string): string {
  if (typeof document === "undefined") return html;

  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");

  // Remove heading anchors
  doc.querySelectorAll(".heading-anchor").forEach((anchor) => {
    anchor.remove();
  });

  // Remove ephemeral UI (editors, indicators)
  // These are temporarily removed before formatting and restored after,
  // but we still need to clean them before saving
  doc.querySelectorAll('[data-ephemeral="true"]').forEach((el) => {
    el.remove();
  });

  return doc.body.innerHTML;
}
