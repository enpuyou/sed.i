"use client";

import { useRef, Suspense, useState } from "react";
import AddContentForm from "@/components/AddContentForm";
import ContentList from "@/components/ContentList";
import Navbar from "@/components/Navbar";
import RecommendedSection from "@/components/RecommendedSection";
import MoodSelector from "@/components/MoodSelector";
import PickForMe from "@/components/PickForMe";
import { ContentItem } from "@/types";
import { useAuth } from "@/contexts/AuthContext";
import Link from "next/link";
import { SHOW_FOR_YOU, SHOW_READING_THEMES } from "@/lib/flags";

export default function DashboardClient() {
  const { user } = useAuth();
  const [showRecommended, setShowRecommended] = useState(false);
  const [showVerificationBanner, setShowVerificationBanner] = useState(() => {
    if (typeof window === "undefined") return false;
    return !localStorage.getItem("verificationBannerDismissed");
  });
  const [mood, setMood] = useState<string | undefined>();
  const contentListRef = useRef<{ addNewItem: (item: ContentItem) => void }>(
    null,
  );

  const handleContentAdded = (newItem: ContentItem) => {
    // Pass the new item to ContentList for optimistic update
    contentListRef.current?.addNewItem(newItem);
  };

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      <Navbar />

      {user && !user.is_verified && showVerificationBanner && (
        <div className="bg-blue-500/10 border-b border-blue-500/20 px-6 py-3 relative">
          <p className="text-center text-sm font-medium text-blue-600 dark:text-blue-400 pr-6">
            Please check your email to verify your account. You won't be able to
            access all features until you do!
          </p>
          <button
            onClick={() => {
              setShowVerificationBanner(false);
              localStorage.setItem("verificationBannerDismissed", "true");
            }}
            className="absolute right-4 top-1/2 -translate-y-1/2 text-blue-600/50 hover:text-blue-600 dark:text-blue-400/50 dark:hover:text-blue-400 transition-colors"
            aria-label="Dismiss"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      )}

      <main className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 py-8">
        <div className="space-y-4">
          {/* Header with Title and Add Form */}
          <div>
            <h1 className="font-serif text-3xl font-normal text-[var(--color-text-primary)] mt-6 min-h-[1.2em]">
              {user &&
                (() => {
                  const hour = new Date().getHours();
                  const greeting =
                    hour < 12
                      ? "Good morning"
                      : hour < 18
                        ? "Good afternoon"
                        : "Good evening";
                  return user.full_name
                    ? `${greeting}, ${user.full_name.split(" ")[0]}`
                    : greeting;
                })()}
            </h1>
            {/* Add Content Form */}
            <div className="mt-2">
              <AddContentForm onContentAdded={handleContentAdded} />
            </div>
          </div>

          {/* Recommended Section */}
          {showRecommended ? (
            <div className="space-y-4 border-t border-[var(--color-border)] pt-6">
              <div>
                <h2 className="font-serif text-xl font-normal text-[var(--color-text-primary)] mb-3">
                  For You
                </h2>
                <MoodSelector mood={mood} setMood={setMood} />
              </div>
              <Suspense
                fallback={
                  <div className="text-center py-8 text-[var(--color-text-muted)]">
                    Loading recommendations...
                  </div>
                }
              >
                <RecommendedSection mood={mood} />
              </Suspense>
              <button
                onClick={() => setShowRecommended(false)}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
              >
                ← Back to all
              </button>
            </div>
          ) : (
            <>
              {/* Quick Actions Row */}
              {(SHOW_FOR_YOU || SHOW_READING_THEMES) && (
                <div className="flex gap-2">
                  {SHOW_FOR_YOU && (
                    <>
                      <button
                        onClick={() => setShowRecommended(true)}
                        className="flex-1 px-4 py-2 rounded text-sm border border-[var(--color-border)] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-colors"
                      >
                        For You ✨
                      </button>
                      <div className="flex-1">
                        <PickForMe />
                      </div>
                    </>
                  )}
                  {SHOW_READING_THEMES && (
                    <Link
                      href="/themes"
                      className="flex-1 px-4 py-2 rounded text-sm border border-[var(--color-border)] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-colors text-center"
                    >
                      Reading themes
                    </Link>
                  )}
                </div>
              )}

              {/* Content List Section */}
              <div className="space-y-4">
                <Suspense
                  fallback={
                    <div className="text-center py-8 text-[var(--color-text-muted)]">
                      Loading...
                    </div>
                  }
                >
                  <ContentList ref={contentListRef} />
                </Suspense>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
