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

// Ask the extension service worker for the ephemeral article it stored in
// chrome.storage.session. Returns null if not in an extension context or no
// article is waiting.
function fetchFromExtension(): Promise<EphemeralArticle | null> {
  return new Promise((resolve) => {
    try {
      if (typeof window === "undefined" || !("chrome" in window)) {
        resolve(null);
        return;
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const cr = (window as any).chrome;
      if (!cr?.runtime?.sendMessage) {
        resolve(null);
        return;
      }
      cr.runtime.sendMessage(
        { action: "getEphemeralArticle" },
        (resp: { article: EphemeralArticle | null }) => {
          if (cr.runtime.lastError) {
            resolve(null);
            return;
          }
          resolve(resp?.article ?? null);
        },
      );
    } catch {
      resolve(null);
    }
  });
}

export default function ReadPage() {
  const [article, setArticle] = useState<EphemeralArticle | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        // 1. Try extension relay (production path from extension popup)
        const fromExtension = await fetchFromExtension();
        if (fromExtension) {
          setArticle(fromExtension);
          return;
        }

        // 2. Fall back to sessionStorage (dev / direct navigation)
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (raw) setArticle(JSON.parse(raw));
      } catch {
        // malformed data — show error state
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
          <Link
            href="/dashboard"
            className="inline-block bg-[var(--color-accent)] text-white px-6 py-2 hover:bg-[var(--color-accent-hover)] transition-colors"
          >
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return <EphemeralReader article={article} />;
}
