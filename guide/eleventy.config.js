import { EleventyHtmlBasePlugin } from "@11ty/eleventy";
import Prism from "prismjs";
import loadLanguages from "prismjs/components/index.js";
import markdownIt from "markdown-it";
import markdownItAnchor from "markdown-it-anchor";

const PART_TITLES = {
  0: "Orientation",
  1: "Ingestion",
  2: "Retrieval",
  3: "The LLM layer",
  4: "Platform",
  5: "Reliability and scale",
  6: "Talking about it",
};

loadLanguages(["python", "sql", "bash", "json", "yaml", "typescript", "tsx", "jsx", "markup", "css", "diff", "toml", "ini"]);

// Highlight at build time. Unknown or plain-text languages are escaped, never passed through.
function highlight(code, lang, md) {
  const grammar = lang && Prism.languages[lang === "html" ? "markup" : lang];
  if (!grammar) {
    return `<pre class="language-text"><code>${md.utils.escapeHtml(code)}</code></pre>`;
  }
  const html = Prism.highlight(code, grammar, lang);
  return `<pre class="language-${lang}"><code class="language-${lang}">${html}</code></pre>`;
}

const PART_BLURBS = {
  0: "What the product is, how the pieces fit together, and the vocabulary.",
  1: "How a link becomes a clean, stored article: saving, extraction, PDFs, the extension.",
  2: "How sed.i finds things: embeddings, chunking, hybrid search, the entity graph, and how it is measured.",
  3: "Where language models are used and how those calls are controlled: the gateway, tagging, memory, the research agent, MCP.",
  4: "The ground everything stands on: the database, background work, security, the frontend, deployment.",
  5: "Where the system is fragile and what was done about it: distributed-systems review, the memory work, the open gaps.",
  6: "How to explain all of it: a system-design walkthrough and the decision log.",
};

export default function (eleventyConfig) {
  // Authors write root-relative links (/retrieval/...); the plugin adds the Pages path prefix.
  eleventyConfig.addPlugin(EleventyHtmlBasePlugin);
  eleventyConfig.addPassthroughCopy({ "src/css": "css" });
  eleventyConfig.addPassthroughCopy({
    "node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2": "fonts/inter-latin-wght-normal.woff2",
    "node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2": "fonts/jetbrains-mono-latin-wght-normal.woff2",
  });

  const md = markdownIt({
    html: true,
    typographer: true,
    highlight: (code, lang) => highlight(code, lang, md),
  }).use(markdownItAnchor, {
    level: [2, 3],
    slugify: (t) =>
      t.toLowerCase().replace(/[^a-z0-9\s-]/g, "").trim().replace(/\s+/g, "-"),
    permalink: markdownItAnchor.permalink.headerLink({ class: "anchor" }),
  });
  eleventyConfig.setLibrary("md", md);

  // Shortcode output is stored and swapped in after the Markdown pass. Returning HTML
  // straight into Markdown lets the outer parse re-read it: a blank line inside a code
  // block would end the HTML block, flatten the indentation and curl the quotes.
  const blocks = new Map();
  let blockId = 0;
  const hold = (html) => {
    const id = ++blockId;
    blocks.set(id, html);
    return `\n\n<!--GUIDE_BLOCK_${id}-->\n\n`;
  };
  eleventyConfig.addTransform("release-blocks", (content, outputPath) => {
    if (!outputPath || !outputPath.endsWith(".html")) return content;
    return content.replace(/<!--GUIDE_BLOCK_(\d+)-->/g, (_, id) => blocks.get(Number(id)) ?? "");
  });

  // Side note, shown as an indented callout.
  eleventyConfig.addPairedShortcode("aside", (content) =>
    hold(`<aside class="note">${md.render(content.trim())}</aside>`));

  // Spoken-style explanation used at the end of every chapter.
  eleventyConfig.addPairedShortcode("say", (content) =>
    hold(`<div class="say"><p class="label">Say it in 60 seconds</p>${md.render(content.trim())}</div>`));

  // Self-test question with the answer hidden.
  eleventyConfig.addPairedShortcode("qa", (content, question) =>
    hold(`<details class="qa"><summary>${md.renderInline(question)}</summary>${md.render(content.trim())}</details>`));

  // Figure with a caption; the body is markdown (usually a fenced block).
  eleventyConfig.addPairedShortcode("fig", (content, caption) =>
    hold(`<figure>${md.render(content.trim())}<figcaption>${md.renderInline(caption)}</figcaption></figure>`));

  eleventyConfig.addCollection("chapters", (api) =>
    api
      .getFilteredByGlob("src/**/*.md")
      .filter((p) => p.data.part !== undefined)
      .sort((a, b) => a.data.part - b.data.part || a.data.order - b.data.order));

  // "On this page": nested list of the h2/h3 headings in rendered chapter HTML.
  eleventyConfig.addFilter("toc", (html) => {
    const items = [...String(html).matchAll(/<h([23]) id="([^"]+)"[^>]*><a class="anchor" href="#[^"]*">([\s\S]*?)<\/a><\/h\1>/g)]
      .map((m) => ({ level: Number(m[1]), id: m[2], text: m[3].replace(/<[^>]+>/g, "") }));
    let out = "<ul>";
    let open = false;
    for (const it of items) {
      if (it.level === 2) {
        if (open) out += "</ul></li>"; else if (out !== "<ul>") out += "</li>";
        out += `<li><a href="#${it.id}">${it.text}</a>`;
        open = false;
      } else {
        if (!open) { out += "<ul>"; open = true; }
        out += `<li><a href="#${it.id}">${it.text}</a></li>`;
      }
    }
    if (open) out += "</ul>";
    return out + (items.length ? "</li>" : "") + "</ul>";
  });
  eleventyConfig.addFilter("ymd", (d) => {
    if (d instanceof Date) return d.toISOString().slice(0, 10);
    return String(d);
  });
  eleventyConfig.addFilter("partTitle", (n) => PART_TITLES[n]);
  eleventyConfig.addFilter("partBlurb", (n) => PART_BLURBS[n]);
  eleventyConfig.addFilter("readMinutes", (text) =>
    Math.max(1, Math.round(String(text || "").split(/\s+/).length / 220)));
  eleventyConfig.addFilter("groupByPart", (chapters) => {
    const parts = new Map();
    for (const c of chapters) {
      if (!parts.has(c.data.part)) parts.set(c.data.part, []);
      parts.get(c.data.part).push(c);
    }
    return [...parts.entries()].map(([part, items]) => ({ part, items }));
  });
  eleventyConfig.addFilter("neighbours", (chapters, url) => {
    const i = chapters.findIndex((c) => c.url === url);
    return { prev: chapters[i - 1], next: chapters[i + 1] };
  });

  return {
    dir: { input: "src", output: "_site", includes: "_includes", data: "_data" },
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk",
  };
}
