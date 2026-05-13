"use client";

import { useState, useEffect, useRef } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import Navbar from "@/components/Navbar";
import ReadingSection from "@/components/settings/ReadingSection";
import FeatureVisibilitySection from "@/components/settings/FeatureVisibilitySection";
import PublicProfileSection from "@/components/settings/PublicProfileSection";
import DangerZone from "@/components/settings/DangerZone";

export default function SettingsPage() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [activeSection, setActiveSection] = useState("reading");
  const readingRef = useRef<HTMLDivElement>(null);
  const featuresRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) setActiveSection(e.target.id);
        }),
      { rootMargin: "-40% 0px -55% 0px" },
    );
    if (readingRef.current) observer.observe(readingRef.current);
    if (featuresRef.current) observer.observe(featuresRef.current);
    if (accountRef.current) observer.observe(accountRef.current);
    return () => observer.disconnect();
  }, []);

  const scrollTo = (ref: React.RefObject<HTMLDivElement | null>) =>
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  const tocItems = [
    { id: "reading", label: "Reading", num: "01", ref: readingRef },
    { id: "features", label: "Features", num: "02", ref: featuresRef },
    { id: "account", label: "Account", num: "03", ref: accountRef },
  ];

  return (
    <div
      className="min-h-screen bg-[var(--color-bg-primary)] flex flex-col"
      suppressHydrationWarning
    >
      <Navbar />

      <main className="flex-1 max-w-4xl mx-auto w-full px-8 py-8">
        <div className="flex gap-12">
          {/* Sticky TOC */}
          <div className="hidden lg:block w-28 flex-shrink-0">
            <div className="sticky top-8 space-y-1">
              {tocItems.map(({ id, label, num, ref }) => (
                <button
                  key={id}
                  onClick={() => scrollTo(ref)}
                  className={`w-full text-left flex items-baseline gap-2 py-1 transition-colors ${
                    activeSection === id
                      ? "text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"
                  }`}
                >
                  <span
                    className={`font-mono text-[11px] ${activeSection === id ? "text-[var(--color-accent)]" : "text-[var(--color-text-faint)]"}`}
                  >
                    {num}
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.15em]">
                    {label}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Sections */}
          <div className="flex-1 space-y-12">
            {/* 01 — Reading */}
            <div id="reading" ref={readingRef} className="space-y-6">
              <div className="flex items-center gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)] whitespace-nowrap">
                  Reading Preferences
                </span>
                <div className="flex-1 border-t border-[var(--color-border)]" />
              </div>
              <ReadingSection isActive={activeSection === "reading"} />
            </div>

            {/* 02 — Features */}
            <div id="features" ref={featuresRef} className="space-y-6">
              <div className="flex items-center gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)] whitespace-nowrap">
                  Feature Visibility
                </span>
                <div className="flex-1 border-t border-[var(--color-border)]" />
              </div>
              <FeatureVisibilitySection />
            </div>

            {/* 03 — Account */}
            <div id="account" ref={accountRef} className="space-y-6">
              <div className="flex items-center gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)] whitespace-nowrap">
                  Account
                </span>
                <div className="flex-1 border-t border-[var(--color-border)]" />
              </div>

              {/* § Session */}
              <div className="mb-8">
                <div className="flex items-baseline gap-3 mb-3">
                  <span className="font-serif text-[var(--color-text-faint)] text-base select-none">
                    §
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)]">
                    Session
                  </span>
                </div>
                <div className="border border-[var(--color-border)] px-5 py-4 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-mono text-xs uppercase tracking-widest text-[var(--color-text-faint)] mb-1">
                      Signed in as
                    </div>
                    <div className="font-serif text-base text-[var(--color-text-primary)]">
                      {user?.email}
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      logout();
                      router.push("/login");
                    }}
                    className="font-mono text-xs uppercase tracking-widest px-3 py-2 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-red-400 hover:text-red-500 transition-colors flex-shrink-0"
                  >
                    Sign out →
                  </button>
                </div>
              </div>

              {/* § Public Profile */}
              <div className="mb-8">
                <div className="flex items-baseline gap-3 mb-3">
                  <span className="font-serif text-[var(--color-text-faint)] text-base select-none">
                    §
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)]">
                    Public Profile
                  </span>
                </div>
                <PublicProfileSection />
              </div>

              {/* § Danger Zone */}
              <div>
                <div className="flex items-baseline gap-3 mb-3">
                  <span className="font-serif text-[var(--color-text-faint)] text-base select-none">
                    §
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-red-500/60">
                    Danger Zone
                  </span>
                </div>
                <DangerZone />
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
