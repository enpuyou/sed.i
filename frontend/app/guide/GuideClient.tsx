"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import SediLogo from "@/components/SediLogo";
import ThemeToggle from "@/components/ThemeToggle";
import NowPlaying from "@/components/NowPlaying";

const sections = [
  { id: "getting-started", title: "Getting Started" },
  { id: "reading", title: "Reading" },
  { id: "crates", title: "Crates" },
  { id: "lists", title: "Lists" },
  { id: "extension", title: "Chrome Extension" },
  { id: "search", title: "Search" },
  { id: "ai", title: "AI-Facilitated Features" },
  { id: "claude-integration", title: "Claude Integration" },
  { id: "public-profiles", title: "Public Profiles" },
  { id: "shortcuts", title: "Keyboard Shortcuts" },
];

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="font-mono text-[11px] bg-[var(--color-bg-tertiary)] border border-[var(--color-border)] px-1.5 py-0.5 text-[var(--color-text-secondary)]">
      {children}
    </kbd>
  );
}

function SectionHeader({
  num,
  id,
  title,
}: {
  num: number;
  id: string;
  title: string;
}) {
  return (
    <div id={id} className="scroll-mt-24">
      <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)]">
        {String(num).padStart(2, "0")}
      </span>
      <h2
        className="mt-2 font-serif text-3xl sm:text-4xl font-normal text-[var(--color-text-primary)]"
        style={{ letterSpacing: "-0.02em" }}
      >
        {title}
      </h2>
    </div>
  );
}

function Feature({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-4 py-3">
      <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] w-28 flex-shrink-0 pt-0.5">
        {label}
      </span>
      <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed flex-1">
        {children}
      </p>
    </div>
  );
}

export default function GuideClient() {
  const { user } = useAuth();
  const [activeSection, setActiveSection] = useState(sections[0].id);
  const observerRef = useRef<IntersectionObserver | null>(null);

  // Track active section via IntersectionObserver
  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
          }
        }
      },
      { rootMargin: "-20% 0px -70% 0px" },
    );

    sections.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observerRef.current!.observe(el);
    });

    return () => observerRef.current?.disconnect();
  }, []);

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      {/* Header */}
      <header className="sticky top-0 z-40 bg-[var(--color-bg-primary)] border-b border-[var(--color-border-subtle)]">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link
            href={user ? "/dashboard" : "/"}
            className="flex items-center gap-2 no-underline hover:opacity-80 transition-opacity"
            style={{ color: "var(--color-text-primary)" }}
          >
            <SediLogo size={18} className="text-[var(--color-text-primary)]" />
            <span
              className="text-lg font-normal"
              style={{ fontFamily: "var(--font-logo)" }}
            >
              sed.i
            </span>
          </Link>
          <div className="ml-6 hidden md:block">
            <NowPlaying />
          </div>
          <div className="flex items-center gap-4 ml-auto">
            <ThemeToggle />
            <Link
              href={user ? "/dashboard" : "/register"}
              className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none bg-[var(--color-bg-secondary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors no-underline"
              style={{ color: "var(--color-text-primary)" }}
            >
              {user ? "Back to app" : "Get started"}
            </Link>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-6 flex gap-12">
        {/* Sidebar TOC — desktop only */}
        <nav className="hidden xl:block w-40 flex-shrink-0 sticky top-20 self-start py-12">
          <ul className="space-y-2">
            {sections.map((s) => (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  className={`font-mono text-[10px] uppercase tracking-wider transition-colors no-underline block py-0.5 ${
                    activeSection === s.id
                      ? "text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"
                  }`}
                  style={{
                    color:
                      activeSection === s.id
                        ? "var(--color-text-primary)"
                        : undefined,
                  }}
                >
                  {s.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        {/* Main content */}
        <main className="flex-1 min-w-0 pt-16 pb-12 space-y-20">
          {/* Intro */}
          <div>
            <h1
              className="text-4xl sm:text-5xl font-normal text-[var(--color-text-primary)]"
              style={{
                fontFamily: "var(--font-logo)",
                letterSpacing: "-0.02em",
              }}
            >
              Guide
            </h1>
            <p className="mt-4 text-[var(--color-text-secondary)] max-w-lg leading-relaxed">
              sed.i is a personal content queue for reading articles, collecting
              vinyl records, and discovering connections between your saved
              ideas.
            </p>
          </div>

          {/* 01 Getting Started */}
          <section>
            <SectionHeader
              num={1}
              id="getting-started"
              title="Getting Started"
            />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Add content">
                Paste any URL into the input field on your dashboard. sed.i
                extracts the article text, images, and metadata automatically.
              </Feature>
              <Feature label="Your queue">
                New articles appear in your reading queue. Filter by{" "}
                <Kbd>All</Kbd>, <Kbd>Unread</Kbd>, or <Kbd>Archived</Kbd>. Sort
                by date, title, or reading time.
              </Feature>
              <Feature label="Read & archive">
                Click any article to open the reader. When you finish, mark it
                as read or archive it to keep your queue clean.
              </Feature>
            </div>
          </section>

          {/* 02 Reading */}
          <section>
            <SectionHeader num={2} id="reading" title="Reading" />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Typography">
                Customize font family (serif, sans, system, Merriweather,
                Verdana), size, line height, letter spacing, and content width
                from the Settings page.
              </Feature>
              <Feature label="Themes">
                Four themes are available: Light, Dark, Sepia, and True Black
                (OLED). The navbar toggle cycles Light/Dark/True Black, and
                Sepia is available in Settings.
              </Feature>
              <Feature label="Focus mode">
                Press <Kbd>f</Kbd> to dim everything except the paragraph
                you&apos;re reading. Nearby paragraphs stay slightly visible for
                context.
              </Feature>
              <Feature label="Bionic reading">
                Enable Bionic mode from Settings to bold the leading part of
                each word and improve scanning on long passages.
              </Feature>
              <Feature label="Highlights">
                Select text to highlight in 5 colors (yellow, green, blue, pink,
                purple). Add notes to any highlight. On desktop, toggle the
                highlights side panel with <Kbd>h</Kbd>.
              </Feature>
              <Feature label="Table of contents">
                An auto-generated table of contents appears on desktop for
                articles with headings. Click any heading to jump to it.
              </Feature>
              <Feature label="TL;DR">
                Use the Generate TL;DR button in the reader to create an AI
                summary before committing to the full read.
              </Feature>
              <Feature label="Similar articles">
                After reading, discover related articles from your queue based
                on semantic similarity (powered by embeddings).
              </Feature>
            </div>
          </section>

          {/* 03 Crates */}
          <section>
            <SectionHeader num={3} id="crates" title="Crates" />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Add records">
                Paste a Discogs release URL to add a vinyl record. sed.i fetches
                cover art, tracklist, genres, styles, and any linked YouTube
                videos.
              </Feature>
              <Feature label="Browse">
                View your collection as a grid. Toggle between loose and tight
                density with <Kbd>d</Kbd>. Search with <Kbd>/</Kbd>.
              </Feature>
              <Feature label="Filter & sort">
                Filter by Collection, Wantlist, or Library. Sort by recently
                added (<Kbd>1</Kbd>), artist (<Kbd>2</Kbd>), or year (
                <Kbd>3</Kbd>).
              </Feature>
              <Feature label="Record detail">
                Click any record to open a gatefold-style detail view with
                tracklist, metadata, style tags, and cover art.
              </Feature>
              <Feature label="Playback">
                Play YouTube videos directly from the tracklist. Queue
                individual tracks or play all. The player persists across pages
                via the navbar.
              </Feature>
              <Feature label="Listening mode">
                Press <Kbd>l</Kbd> to enter a full-screen immersive listening
                view with large album art, transport controls, and progress bar.
              </Feature>
            </div>
          </section>

          {/* 04 Lists */}
          <section>
            <SectionHeader num={4} id="lists" title="Lists" />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Create lists">
                Organize your saved content into named lists. Add a description
                to remember the purpose of each collection.
              </Feature>
              <Feature label="Add content">
                Add articles to any list from the content card menu or from
                within the reader.
              </Feature>
              <Feature label="List management">
                Rename lists, update descriptions, add or remove articles, and
                iterate on drafts from one workspace.
              </Feature>
              <Feature label="Writing draft">
                Each list has a writing workspace — a full-screen markdown
                editor for composing an essay or post based on the articles
                you&apos;ve collected. Open it from the list page. Your draft
                auto-saves as you type and persists across sessions.
              </Feature>
            </div>
          </section>

          {/* 05 Chrome Extension */}
          <section>
            <SectionHeader num={5} id="extension" title="Chrome Extension" />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Installation">
                Visit the{" "}
                <a
                  href="https://chromewebstore.google.com/detail/sedi/doojneiapaegndmglponeacdbcgaojnm"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[var(--color-accent)] hover:underline"
                >
                  Chrome Web Store page
                </a>{" "}
                and click "Add to Chrome" to use the official extension.
              </Feature>
              <Feature label="Capability">
                Once installed, the extension allows you to save any article you
                are currently reading to your sed.i queue with a single click
                without leaving the page.
              </Feature>
            </div>
          </section>

          {/* 06 Search */}
          <section>
            <SectionHeader num={6} id="search" title="Search" />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Semantic search">
                Search uses AI embeddings to find articles by meaning, not just
                keywords. Type a concept and find related articles even if the
                exact words don&apos;t match.
              </Feature>
              <Feature label="Global search">
                Access search from the navbar on any page. Results show
                semantically related articles with match scores.
              </Feature>
            </div>
          </section>

          {/* 07 AI Features */}
          <section>
            <SectionHeader num={7} id="ai" title="AI-Facilitated Features" />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Auto-tagging">
                Articles are automatically tagged with relevant topics using AI
                analysis of the content.
              </Feature>
              <Feature label="Connections">
                Connections is an experimental feature we&apos;re still
                building. It helps surface links between your highlights across
                different articles and shows semantically related passages.
              </Feature>
            </div>
          </section>

          {/* 08 Claude Integration */}
          <section>
            <SectionHeader
              num={8}
              id="claude-integration"
              title="Claude Integration"
            />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="MCP endpoint">
                sed.i connects to Claude via the Model Context Protocol. The
                endpoint is{" "}
                <code className="font-mono text-xs bg-[var(--color-bg-tertiary)] px-1 py-0.5">
                  https://api.read-sedi.com/mcp-transport
                </code>
                . Both Claude Desktop and claude.ai web are supported.
              </Feature>
              <Feature label="Claude Desktop">
                Edit{" "}
                <code className="font-mono text-xs bg-[var(--color-bg-tertiary)] px-1 py-0.5">
                  ~/Library/Application
                  Support/Claude/claude_desktop_config.json
                </code>{" "}
                and add sed.i as an MCP server with{" "}
                <code className="font-mono text-xs bg-[var(--color-bg-tertiary)] px-1 py-0.5">
                  &quot;type&quot;: &quot;http&quot;
                </code>{" "}
                and the endpoint URL above. Restart Claude Desktop — it will
                open a browser window to authorize with your sed.i account.
              </Feature>
              <Feature label="claude.ai web">
                Go to Settings → Integrations → Add custom integration. Paste
                the endpoint URL and click Connect. An authorization window will
                appear — log in with your sed.i account to complete the
                connection.
              </Feature>
              <Feature label="What you can ask">
                Once connected, try: &ldquo;Summarize my &lsquo;[list
                name]&rsquo; list and tell me what my draft is missing&rdquo; —
                or &ldquo;Search my library for articles about [topic]&rdquo; —
                or &ldquo;What have I been highlighting about [topic]?&rdquo; —
                or &ldquo;Which of my lists has the most unread articles?&rdquo;
              </Feature>
              <Feature label="Write tools">
                Claude can do more than read. Ask it to save a URL to your
                library, create a new list, or update your draft for a list.
                Every action goes through the same account you authorized, so
                changes show up in sed.i immediately.
              </Feature>
            </div>
          </section>

          {/* 09 Public Profiles */}
          <section>
            <SectionHeader
              num={9}
              id="public-profiles"
              title="Public Profiles"
            />
            <div className="mt-6 space-y-1 border-t border-[var(--color-border-subtle)] pt-4">
              <Feature label="Claim Username">
                Go to your Profile Settings to claim a unique username and
                toggle your profile visibility.
              </Feature>
              <Feature label="Share Content">
                Toggle individual articles and vinyl records to "Public" so
                others can discover them on your profile page.
              </Feature>
            </div>
          </section>

          {/* 10 Keyboard Shortcuts */}
          <section>
            <SectionHeader num={10} id="shortcuts" title="Keyboard Shortcuts" />
            <div className="mt-6 border-t border-[var(--color-border-subtle)] pt-4">
              {/* Reader */}
              <h3 className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] mb-3">
                Reader
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-8 gap-y-2 mb-8">
                {[
                  ["f", "Toggle focus mode"],
                  ["h", "Toggle highlights panel (desktop)"],
                  ["c", "Toggle connections panel (desktop, experiment)"],
                  ["?", "Shortcuts help"],
                  ["Esc", "Back to queue"],
                ].map(([key, desc]) => (
                  <div key={key} className="flex items-center gap-2">
                    <Kbd>{key}</Kbd>
                    <span className="text-xs text-[var(--color-text-secondary)]">
                      {desc}
                    </span>
                  </div>
                ))}
              </div>

              {/* Crates */}
              <h3 className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] mb-3">
                Crates
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-8 gap-y-2 mb-8">
                {[
                  ["/", "Focus search"],
                  ["1", "Sort by added"],
                  ["2", "Sort by artist"],
                  ["3", "Sort by year"],
                  ["d", "Toggle density"],
                  ["l", "Listening mode"],
                  ["?", "Shortcuts help"],
                  ["Esc", "Clear / close"],
                ].map(([key, desc]) => (
                  <div key={key} className="flex items-center gap-2">
                    <Kbd>{key}</Kbd>
                    <span className="text-xs text-[var(--color-text-secondary)]">
                      {desc}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* Footer */}
          <div className="border-t border-[var(--color-border-subtle)] pt-8 pb-12 flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)]">
              sed.i
            </span>
            <Link
              href={user ? "/dashboard" : "/"}
              className="compact-touch font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-accent)] transition-colors no-underline"
            >
              {user ? "Back to app" : "Home"}
            </Link>
          </div>
        </main>
      </div>
    </div>
  );
}
