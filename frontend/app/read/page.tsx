"use client";

import { useState, useEffect } from "react";
import EphemeralReader from "@/components/EphemeralReader";
import Link from "next/link";

interface EphemeralArticle {
  url: string;
  html: string;
  title?: string;
  author?: string;
  description?: string;
  thumbnail?: string;
  publishedDate?: string;
}

const STORAGE_KEY = "sedi_ephemeral_article";

function isInIframe() {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

export default function ReadPage() {
  const [article, setArticle] = useState<EphemeralArticle | null>(null);
  const [loading, setLoading] = useState(true);
  const inIframe = typeof window !== "undefined" && isInIframe();

  useEffect(() => {
    function load() {
      try {
        // 1. Read from URL hash (set by extension popup / overlay)
        const hash = window.location.hash.slice(1);
        if (hash) {
          const parsed = JSON.parse(decodeURIComponent(hash));
          history.replaceState(null, "", window.location.pathname);
          setArticle(parsed);
          return;
        }

        // 2. Fall back to sessionStorage (dev / direct navigation)
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (raw) setArticle(JSON.parse(raw));
      } catch {
        // malformed data — show empty state
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center">
        <div className="flex items-center justify-center gap-1 text-[var(--color-text-muted)] font-mono">
          <span className="inline-block animate-pulse">.</span>
          <span className="inline-block animate-pulse [animation-delay:0.3s]">
            .
          </span>
          <span className="inline-block animate-pulse [animation-delay:0.6s]">
            .
          </span>
        </div>
      </div>
    );
  }

  if (!article) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center">
        <div className="text-center max-w-md px-4">
          <h1 className="font-serif text-2xl font-normal text-[var(--color-text-primary)] mb-2">
            No article to read
          </h1>
          <p className="text-[var(--color-text-secondary)] mb-6">
            Open this page from the sed.i browser extension to read an article.
          </p>
          {!inIframe && (
            <Link
              href="/dashboard"
              className="inline-block bg-[var(--color-accent)] text-white px-6 py-2 hover:bg-[var(--color-accent-hover)] transition-colors"
            >
              Back to Dashboard
            </Link>
          )}
        </div>
      </div>
    );
  }

  return <EphemeralReader article={article} />;
}
