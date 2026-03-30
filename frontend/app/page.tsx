"use client";

import ThemeToggle from "@/components/ThemeToggle";
import { usePlayer } from "@/contexts/PlayerContext";
import { useEffect, useRef } from "react";
import BackgroundDecoration from "@/components/BackgroundDecoration";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import Link from "next/link";
import SediLogo from "@/components/SediLogo";
import RetroLoader from "@/components/RetroLoader";
import FeatureShowcase from "@/components/FeatureShowcase";
import {
  SavePlaceholder,
  ReadPlaceholder,
  ListenPlaceholder,
  ClaudePlaceholder,
  WritePlaceholder,
} from "@/components/FeaturePlaceholders";

const showcaseFeatures = [
  {
    num: "01",
    title: "Save",
    description:
      "One click saving with Chrome Extension or paste any URL in app. Article text, images, and metadata extracted automatically.",
    detail: "Chrome Extension · Paste URL · Auto-extract",
    clipSrc: "/clips/01-save",
    placeholder: <SavePlaceholder />,
  },
  {
    num: "02",
    title: "Read",
    description:
      "Highlight passages, add notes, enter focus mode. Customizable typography for nondistracting reading experience",
    detail: "Typography · Highlights · Focus",
    clipSrc: "/clips/02-read",
    placeholder: <ReadPlaceholder />,
  },
  {
    num: "03",
    title: "Connect to Your LLM",
    description:
      "Talk to your reading list. Summarize, create lists, send drafts — through MCP in your favorite LLM.",
    detail: "MCP · Claude",
    clipSrc: "/clips/04-llm",
    placeholder: <ClaudePlaceholder />,
  },
  {
    num: "04",
    title: "Write",
    description:
      "Brainstorm your draft through LLM. Write alongside your sources. Markdown editor with highlights as references. ",
    detail: "Editor · Source Pane · Auto-save",
    clipSrc: "/clips/05-write",
    placeholder: <WritePlaceholder />,
  },
  {
    num: "05",
    title: "Listen",
    description:
      "A vinyl collection from Discogs. Browse crates, queue tracks, play audio in app through YouTube.",
    detail: "Crates · Player · Listening Mode",
    clipSrc: "/clips/03-listen",
    placeholder: <ListenPlaceholder />,
  },
];

export default function Home() {
  const { user, isLoading } = useAuth();
  const { addToQueue, setIndex, queue } = usePlayer();
  const router = useRouter();
  const hasInitialized = useRef(false);

  useEffect(() => {
    if (!isLoading && user) {
      router.push("/dashboard");
    }
  }, [user, isLoading, router]);

  // Pre-load specific track for demo (only if queue is empty)
  useEffect(() => {
    if (hasInitialized.current) return;

    // Check if we already have the specific track or if queue is empty
    const demoTrackId = "ProvVFrF6b8";
    const hasTrack = queue.some((t) => t.videoId === demoTrackId);

    if (!hasTrack && queue.length === 0) {
      addToQueue({
        videoId: demoTrackId,
        title: "A・I・R (Air In Resort)",
        artist: "Hiroshi Yoshimura",
        cover_url: "https://www.jazzmessengers.com/80176/air-in-resort.jpg",
      });
      // Set as current track (index 0) but DO NOT play
      // We need a small timeout to allow state update if batched, but usually safe
      setTimeout(() => setIndex(0), 100);
      hasInitialized.current = true;
    } else if (hasTrack) {
      // If track exists, ensure it's selected if nothing else is playing?
      // User just wants it ready. If they navigated away and back, it might still be there.
      hasInitialized.current = true;
    }
  }, [addToQueue, setIndex, queue]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg-primary)]">
        <RetroLoader
          text="loading"
          className="text-sm text-[var(--color-text-secondary)]"
        />
      </div>
    );
  }

  return (
    <div className="bg-[var(--color-bg-primary)] min-h-screen relative">
      <BackgroundDecoration />

      {/* Top Right: Theme Toggle — aligned with GlobalPlayer's h-14 bar */}
      <div
        className="fixed top-0 right-0 z-50 animate-reveal-fade px-6 h-14 flex items-center"
        style={{ animationDelay: "0.5s" }}
      >
        <ThemeToggle />
      </div>

      {/* Hero — full viewport */}
      <section className="relative z-10 flex flex-col items-center justify-center min-h-screen px-6 text-center pointer-events-none">
        <div
          className="animate-reveal-fade pointer-events-auto"
          style={{ animationDelay: "0.1s" }}
        >
          <SediLogo size={100} className="text-[var(--color-text-primary)]" />
        </div>

        <h1
          className="mt-8 text-6xl sm:text-7xl font-normal tracking-tight text-[var(--color-text-primary)] animate-reveal-up pointer-events-auto"
          style={{
            fontFamily: "var(--font-logo), Georgia, serif",
            animationDelay: "0.3s",
          }}
        >
          sed.i
        </h1>

        <div className="mt-8 flex items-center gap-4 sm:gap-6 pointer-events-auto">
          {["CURATE", "READ", "LISTEN"].map((word, i) => (
            <span
              key={word}
              className="font-mono text-[12px] tracking-[0.2em] text-[var(--color-text-muted)] animate-reveal-up"
              style={{ animationDelay: `${0.6 + i * 0.15}s` }}
            >
              {word}.
            </span>
          ))}
        </div>

        <div
          className="mt-12 flex items-center gap-4 animate-reveal-up pointer-events-auto"
          style={{ animationDelay: "1.1s" }}
        >
          <Link
            href="/login"
            className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none bg-[var(--color-bg-secondary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors no-underline"
            style={{ color: "var(--color-text-primary)" }}
          >
            Log in
          </Link>
          <Link
            href="/register"
            className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none bg-[var(--color-bg-secondary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors no-underline"
            style={{ color: "var(--color-text-primary)" }}
          >
            Sign up
          </Link>
        </div>

        {/* Scroll hint */}
        <div
          className="absolute bottom-8 animate-reveal-fade"
          style={{ animationDelay: "1.6s" }}
        >
          <div className="w-px h-8 bg-[var(--color-border)] mx-auto" />
        </div>
      </section>

      {/* Feature showcase — stacking cards */}
      <FeatureShowcase
        features={showcaseFeatures.map((f) => ({
          ...f,
          placeholderContent: f.placeholder,
        }))}
      />

      {/* CTA section — images show through (no bg), z above decoration */}
      <div className="relative min-h-screen flex flex-col items-center justify-center px-6 text-center" style={{ zIndex: 20 }}>
        <div className="relative py-24">
          <SediLogo size={100} className="text-[var(--color-text-primary)] mx-auto" />

          <h2
            className="mt-6 font-serif text-5xl sm:text-6xl font-normal text-[var(--color-text-primary)]"
            style={{ letterSpacing: "-0.02em", fontFamily: "var(--font-logo), Georgia, serif" }}
          >
            sed.i
          </h2>

          <p
            className="mt-4 text-base text-[var(--color-text-secondary)] max-w-xs mx-auto leading-relaxed"
            style={{ fontFamily: "'Helvetica Neue', Helvetica, Arial, sans-serif", fontWeight: "var(--feature-desc-weight)", letterSpacing: "var(--feature-desc-spacing)" } as React.CSSProperties}
          >
            A personal queue for reading, listening, and thinking.
          </p>

          <div className="mt-10 flex items-center justify-center gap-4">
            <Link
              href="/register"
              className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none bg-[var(--color-bg-secondary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors no-underline"
              style={{ color: "var(--color-text-primary)" }}
            >
              Get started
            </Link>
            <Link
              href="/login"
              className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none bg-[var(--color-bg-secondary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors no-underline"
              style={{ color: "var(--color-text-primary)" }}
            >
              Log in
            </Link>
          </div>

          <div className="mt-16 flex items-center justify-center gap-8">
            <Link
              href="/guide"
              className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors no-underline"
            >
              Guide
            </Link>
            <span className="text-[var(--color-border)]">·</span>
            <a
              href="https://chromewebstore.google.com/detail/sedi/doojneiapaegndmglponeacdbcgaojnm"
              className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors no-underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              Chrome Extension
            </a>
            <span className="text-[var(--color-border)]">·</span>
            <Link
              href="/guide#claude-integration"
              className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors no-underline"
            >
              MCP
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
