// Checks the guide's source and built output. Run after `npm run build`:  node scripts/check.mjs
import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";

const guideDir = path.resolve(import.meta.dirname, "..");
const repoRoot = path.resolve(guideDir, "..");
const srcDir = path.join(guideDir, "src");
const siteDir = process.env.GUIDE_SITE || path.join(guideDir, "_site");
const sourceOnly = process.argv.includes("--source-only");
const problems = [];
const warnings = [];
const fail = (file, msg) => problems.push(`${path.relative(repoRoot, file)}: ${msg}`);
const warn = (file, msg) => warnings.push(`${path.relative(repoRoot, file)}: ${msg}`);

const walk = (dir, out = []) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    e.isDirectory() ? walk(p, out) : out.push(p);
  }
  return out;
};

const repoFiles = execSync("git ls-files", { cwd: repoRoot, maxBuffer: 1 << 26 })
  .toString().split("\n").filter(Boolean);
const baseNames = new Set(repoFiles.map((f) => path.basename(f)));
const norm = (l) => l.trim();

function frontMatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n/);
  const data = {};
  if (m) for (const line of m[1].split("\n")) {
    const kv = line.match(/^(\w+):\s*(.*)$/);
    if (kv) data[kv[1]] = kv[2].replace(/^["']|["']$/g, "");
  }
  return { data, body: m ? text.slice(m[0].length) : text };
}

// ---------- source checks ----------
const mdFiles = walk(srcDir).filter((f) => f.endsWith(".md"));
const REQUIRED_H2 = [
  /^## In one minute$/m, /^## The problem$/m, /^## Mental model$/m, /^## Walkthrough/m,
  /^## How it is built here$/m, /^## Design choices$/m, /^## How it fails$/m,
  /^## What is weak or missing$/m, /^## Check yourself$/m,
];

for (const file of mdFiles) {
  const text = fs.readFileSync(file, "utf8");
  const { data, body } = frontMatter(text);
  if (data.part === undefined) continue; // home page etc.
  for (const k of ["title", "part", "order", "summary", "verified", "permalink", "layout"]) {
    if (!data[k]) fail(file, `front matter is missing "${k}"`);
  }
  if (data.anatomy !== "custom") {
    for (const re of REQUIRED_H2) if (!re.test(body)) fail(file, `missing section matching ${re}`);
    if (!/\{%\s*say\s*%\}/.test(body)) fail(file, 'missing {% say %} block ("Say it in 60 seconds")');
    const qa = (body.match(/\{%\s*qa\s+"/g) || []).length;
    if (qa < 4) fail(file, `needs at least 4 {% qa %} questions (has ${qa})`);
  }
  const words = body.split(/\s+/).length;
  if (words < 1200) warn(file, `short chapter (${words} words)`);
  if (words > 7000) warn(file, `very long chapter (${words} words); consider splitting`);
  if (/\p{Extended_Pictographic}/u.test(body)) fail(file, "contains emoji");
  if (/\b(TODO|TBD|FIXME|lorem ipsum)\b/i.test(body)) fail(file, "contains TODO/TBD placeholder text");

  // template braces inside code fences must be wrapped in {% raw %}
  const fenceRe = /```[\w-]*\n([\s\S]*?)\n```/g;
  for (let m; (m = fenceRe.exec(body)); ) {
    if (/\{\{|\{%/.test(m[1])) {
      const before = body.slice(0, m.index);
      const open = (before.match(/\{%\s*raw\s*%\}/g) || []).length;
      const close = (before.match(/\{%\s*endraw\s*%\}/g) || []).length;
      if (open <= close) fail(file, "a code block contains {{ or {% outside {% raw %}...{% endraw %}");
    }
  }

  // verbatim excerpts: <!--src path#a-b--> followed by a fenced block
  const srcRe = /<!--src ([^\s#>]+)(?:#(\d+)-(\d+))?-->\s*\n```[\w-]*\n([\s\S]*?)\n```/g;
  for (let m; (m = srcRe.exec(body)); ) {
    const [, rel, a, b, block] = m;
    const abs = path.join(repoRoot, rel);
    if (!fs.existsSync(abs)) { fail(file, `excerpt source not found: ${rel}`); continue; }
    let fileLines = fs.readFileSync(abs, "utf8").split("\n");
    if (a) fileLines = fileLines.slice(Number(a) - 1, Number(b));
    const hay = fileLines.map(norm).filter(Boolean);
    const segments = [[]];
    for (const line of block.split("\n")) {
      const t = norm(line);
      if (/^(#|\/\/|--)?\s*\.\.\.\s*(\*\/)?$/.test(t)) segments.push([]);
      else if (t) segments[segments.length - 1].push(t);
    }
    let from = 0;
    for (const seg of segments.filter((s) => s.length)) {
      let found = -1;
      for (let i = from; i + seg.length <= hay.length && found < 0; i++) {
        if (seg.every((l, j) => hay[i + j] === l)) found = i;
      }
      if (found < 0) { fail(file, `excerpt is not verbatim from ${rel}${a ? `#${a}-${b}` : ""}: "${seg[0].slice(0, 60)}"`); break; }
      from = found + seg.length;
    }
  }

  // file paths in inline code must exist
  const pathRe = /`([A-Za-z0-9_@./-]+\.(?:py|ts|tsx|js|mjs|md|toml|json|ya?ml|sql|css|html|sh|njk|txt))`/g;
  for (let m; (m = pathRe.exec(body)); ) {
    const p = m[1];
    if (p.startsWith("http") || p.includes("*")) continue;
    const ok = p.includes("/")
      ? repoFiles.some((f) => f === p || f.endsWith("/" + p))
      : baseNames.has(p);
    if (!ok) fail(file, `file mentioned in code does not exist in the repo: ${p}`);
  }
}

// ---------- built output checks ----------
if (!sourceOnly && fs.existsSync(siteDir)) {
  const htmlFiles = walk(siteDir).filter((f) => f.endsWith(".html") && !f.includes("pagefind"));
  const exists = (urlPath) => {
    const clean = decodeURIComponent(urlPath.split("#")[0].split("?")[0]);
    const p = path.join(siteDir, clean);
    return fs.existsSync(p) && (fs.statSync(p).isFile() || fs.existsSync(path.join(p, "index.html")));
  };
  for (const file of htmlFiles) {
    const html = fs.readFileSync(file, "utf8");
    const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
    for (const m of html.matchAll(/\bhref="([^"]+)"/g)) {
      const href = m[1];
      if (/^(https?:|mailto:|tel:)/.test(href)) continue;
      if (href.startsWith("#")) { if (!ids.has(decodeURIComponent(href.slice(1)))) fail(file, `broken anchor ${href}`); continue; }
      if (href.startsWith("/")) { if (!exists(href)) fail(file, `broken link ${href}`); }
    }
  }
}

for (const w of warnings) console.log("warning:", w);
if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const p of problems) console.error(" -", p);
  process.exit(1);
}
console.log(`ok: ${mdFiles.length} source files checked${!sourceOnly && fs.existsSync(siteDir) ? ", built links checked" : ""}`);
