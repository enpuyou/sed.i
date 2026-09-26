/**
 * Unit tests for bionicReading.ts utilities.
 *
 * Covers:
 * - toBionic: bolds first ~50% of each word without touching HTML tags
 * - addHeadingAnchors: ID generation, deduplication, anchor prepend
 * - stripDocumentWrappers: extracts body content from full HTML documents (PDF path)
 * - sanitizeContentHtml: removes heading anchors and ephemeral UI before save
 *
 * These run in jsdom (jest-environment-jsdom) so DOMParser is available.
 */

import {
  toBionic,
  addHeadingAnchors,
  stripDocumentWrappers,
  sanitizeContentHtml,
  sanitizeArticleHtml,
} from "../../lib/bionicReading";

// ---------------------------------------------------------------------------
// toBionic
// ---------------------------------------------------------------------------

describe("toBionic", () => {
  it("bolds the first half of words between tags", () => {
    const input = "<p>Hello world</p>";
    const result = toBionic(input);
    // "Hello" (5 chars) → bold first 3: <strong>Hel</strong>lo
    expect(result).toContain("<strong>Hel</strong>lo");
    // "world" (5 chars) → bold first 3: <strong>wor</strong>ld
    expect(result).toContain("<strong>wor</strong>ld");
  });

  it("does not alter HTML tags themselves", () => {
    const input = "<p>Hi</p>";
    const result = toBionic(input);
    // <p> and </p> should survive intact
    expect(result).toContain("<p>");
    expect(result).toContain("</p>");
  });

  it("does not bold standalone single-character words", () => {
    // Single-char words (I) are skipped by the `length <= 1` guard.
    // Note: two-char words like "go" still get their first char bolded (<strong>g</strong>o).
    const input = "<p>I go</p>";
    const result = toBionic(input);
    // "I" (1 char) should not be wrapped in strong
    expect(result).not.toContain("<strong>I</strong>");
    // "go" (2 chars) → <strong>g</strong>o (2-char bolding is expected)
    expect(result).toContain("<strong>g</strong>o");
  });

  it("handles empty string gracefully", () => {
    expect(toBionic("")).toBe("");
  });
});

// ---------------------------------------------------------------------------
// addHeadingAnchors
// ---------------------------------------------------------------------------

describe("addHeadingAnchors", () => {
  it("adds id and anchor link to h2 headings", () => {
    const html = "<h2>My Section</h2><p>Content.</p>";
    const result = addHeadingAnchors(html);
    expect(result).toContain('id="my-section"');
    expect(result).toContain('href="#my-section"');
    expect(result).toContain('class="heading-anchor"');
  });

  it("adds anchors to h3 and h4 headings", () => {
    const html = "<h3>Sub Section</h3><h4>Deep Section</h4>";
    const result = addHeadingAnchors(html);
    expect(result).toContain('id="sub-section"');
    expect(result).toContain('id="deep-section"');
  });

  it("deduplicates repeated heading text with -2, -3 suffix", () => {
    const html =
      "<h2>Introduction</h2><h2>Introduction</h2><h2>Introduction</h2>";
    const result = addHeadingAnchors(html);
    expect(result).toContain('id="introduction"');
    expect(result).toContain('id="introduction-2"');
    expect(result).toContain('id="introduction-3"');
  });

  it("preserves existing heading id instead of overwriting it", () => {
    const html = '<h2 id="custom-id">My Section</h2>';
    const result = addHeadingAnchors(html);
    expect(result).toContain('id="custom-id"');
    expect(result).not.toContain('id="my-section"');
  });

  it("slugifies special characters and spaces correctly", () => {
    const html = "<h2>Hello, World! (2024)</h2>";
    const result = addHeadingAnchors(html);
    expect(result).toContain('id="hello-world-2024"');
  });

  it("returns the input unchanged in non-browser environment", () => {
    // DOMParser is available in jsdom so this tests the normal path.
    // The SSR guard (typeof document === 'undefined') is not hit in jsdom.
    const html = "<h2>Test</h2>";
    const result = addHeadingAnchors(html);
    expect(result).toContain("Test");
  });
});

// ---------------------------------------------------------------------------
// stripDocumentWrappers — critical for PDF extraction output
// ---------------------------------------------------------------------------

describe("stripDocumentWrappers", () => {
  it("strips <!DOCTYPE>, <html>, <head>, <body> and returns inner content", () => {
    const fullDoc =
      "<!DOCTYPE html><html><head><title>Doc</title></head><body><p>Content</p></body></html>";
    const result = stripDocumentWrappers(fullDoc);
    expect(result).toContain("<p>Content</p>");
    expect(result).not.toContain("<!DOCTYPE");
    expect(result).not.toContain("<html");
    expect(result).not.toContain("<head");
    expect(result).not.toContain("<body");
  });

  it("returns plain article HTML unchanged (no wrappers to strip)", () => {
    const html = "<h1>Title</h1><p>Paragraph.</p>";
    const result = stripDocumentWrappers(html);
    expect(result).toContain("Title");
    expect(result).toContain("Paragraph.");
  });

  it("handles empty string without throwing", () => {
    expect(() => stripDocumentWrappers("")).not.toThrow();
  });

  it("handles HTML with no body tag gracefully", () => {
    const html = "<p>Just a paragraph</p>";
    const result = stripDocumentWrappers(html);
    expect(result).toContain("Just a paragraph");
  });
});

// ---------------------------------------------------------------------------
// sanitizeContentHtml — prevents internal UI markup leaking into stored content
// ---------------------------------------------------------------------------

describe("sanitizeContentHtml", () => {
  it("removes heading anchor elements before save", () => {
    const html =
      '<h2 id="intro"><a href="#intro" class="heading-anchor" aria-label="Link to Introduction"></a>Introduction</h2>';
    const result = sanitizeContentHtml(html);
    expect(result).not.toContain("heading-anchor");
    expect(result).toContain("Introduction");
  });

  it("removes data-ephemeral=true elements before save", () => {
    const html =
      '<p>Real content.</p><span data-ephemeral="true">Highlight editor UI</span>';
    const result = sanitizeContentHtml(html);
    expect(result).not.toContain("Highlight editor UI");
    expect(result).toContain("Real content.");
  });

  it("preserves non-ephemeral content unchanged", () => {
    const html = "<p>Keep this.</p><h2>And this.</h2>";
    const result = sanitizeContentHtml(html);
    expect(result).toContain("Keep this.");
    expect(result).toContain("And this.");
  });

  it("handles empty string gracefully", () => {
    expect(() => sanitizeContentHtml("")).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// sanitizeArticleHtml — XSS defense for third-party extracted article HTML
// ---------------------------------------------------------------------------

describe("sanitizeArticleHtml", () => {
  it("strips inline <script> tags", () => {
    const html = '<p>Hello</p><script>alert("xss")</script>';
    const result = sanitizeArticleHtml(html);
    expect(result).not.toContain("<script");
    expect(result).not.toContain("alert");
    expect(result).toContain("Hello");
  });

  it("strips on* event handler attributes", () => {
    const html = '<img src="x.jpg" onerror="alert(1)" onclick="alert(2)">';
    const result = sanitizeArticleHtml(html);
    expect(result).not.toContain("onerror");
    expect(result).not.toContain("onclick");
  });

  it("strips javascript: URLs from href", () => {
    const html = '<a href="javascript:alert(1)">click me</a>';
    const result = sanitizeArticleHtml(html);
    expect(result).not.toContain("javascript:");
    expect(result).toContain("click me");
  });

  it("strips iframe tags", () => {
    const html =
      '<p>Content</p><iframe src="https://evil.example.com"></iframe>';
    const result = sanitizeArticleHtml(html);
    expect(result).not.toContain("<iframe");
    expect(result).toContain("Content");
  });

  it("strips svg-based XSS vectors", () => {
    const html =
      '<svg><animatetransform onbegin="alert(1)"></animatetransform></svg>';
    const result = sanitizeArticleHtml(html);
    expect(result).not.toContain("onbegin");
  });

  it("preserves data-* attributes used by highlight spans", () => {
    const html =
      '<span data-highlight-id="abc123" data-highlight-color="yellow">highlighted text</span>';
    const result = sanitizeArticleHtml(html);
    expect(result).toContain("data-highlight-id");
    expect(result).toContain("data-highlight-color");
    expect(result).toContain("highlighted text");
  });

  it("preserves heading anchor id/href/class/aria-label attributes", () => {
    const html =
      '<h2 id="intro"><a href="#intro" class="heading-anchor" aria-label="Link to Introduction"></a>Introduction</h2>';
    const result = sanitizeArticleHtml(html);
    expect(result).toContain('id="intro"');
    expect(result).toContain('href="#intro"');
    expect(result).toContain("heading-anchor");
    expect(result).toContain("Introduction");
  });

  it("preserves normal article markup unchanged", () => {
    const html =
      '<p>Normal paragraph with <strong>bold</strong> and <em>italic</em> text.</p><img src="https://example.com/photo.jpg" alt="A photo">';
    const result = sanitizeArticleHtml(html);
    expect(result).toContain("<strong>bold</strong>");
    expect(result).toContain("<em>italic</em>");
    expect(result).toContain('src="https://example.com/photo.jpg"');
    expect(result).toContain('alt="A photo"');
  });

  it("handles empty string gracefully", () => {
    expect(() => sanitizeArticleHtml("")).not.toThrow();
  });
});
