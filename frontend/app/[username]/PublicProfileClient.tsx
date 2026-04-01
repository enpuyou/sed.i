"use client";

import { useState, useEffect } from "react";
import ProfileNavbar from "@/components/ProfileNavbar";
import ContentItem from "@/components/ContentItem";
import ContentIndexItem from "@/components/ContentIndexItem";
import VinylCard from "@/components/VinylCard";
import RetroLoader from "@/components/RetroLoader";
import { User, ContentItem as ContentItemType, VinylRecord } from "@/types";
import { publicAPI } from "@/lib/api";

type Tab = "queue" | "crates";
type ViewMode = "list" | "index";

export default function PublicProfileClient({
  username,
}: {
  username: string;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<User | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("queue");
  const [viewMode, setViewMode] = useState<ViewMode>("list");

  const [queue, setQueue] = useState<ContentItemType[]>([]);
  const [vinyl, setVinyl] = useState<VinylRecord[]>([]);
  const [showQueueTab, setShowQueueTab] = useState(false);
  const [showCratesTab, setShowCratesTab] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("contentListViewMode");
    if (saved === "index") setViewMode("index");
  }, []);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const userProfile = await publicAPI.getProfile(username);
        setProfile(userProfile);

        const [qRes, vRes] = await Promise.allSettled([
          publicAPI.getPublicContent(username),
          publicAPI.getPublicVinyl(username),
        ]);

        const queueVisible = qRes.status === "fulfilled";
        const cratesVisible = vRes.status === "fulfilled";

        setShowQueueTab(queueVisible);
        setShowCratesTab(cratesVisible);
        setQueue(queueVisible ? qRes.value.items || [] : []);
        setVinyl(cratesVisible ? vRes.value || [] : []);

        if (queueVisible) setActiveTab("queue");
        else if (cratesVisible) setActiveTab("crates");
      } catch (err) {
        const e = err as { response?: { status?: number } };
        if (e.response?.status === 404) setError("Profile not found.");
        else if (e.response?.status === 403)
          setError("This profile is private.");
        else setError("Couldn't load profile.");
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [username]);

  const noop = () => {};
  const noopId = (_id: string) => {};

  if (!loading && (error || !profile)) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)] flex flex-col">
        <ProfileNavbar username={username} />
        <main className="flex-1 flex flex-col items-center justify-center p-8 text-center max-w-md mx-auto">
          <h1 className="font-serif text-2xl text-[var(--color-text-primary)] mb-2">
            {error || "Profile not found"}
          </h1>
          <p className="text-[var(--color-text-muted)] text-sm">
            This account either doesn&apos;t exist or has chosen to keep their
            profile private.
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)] flex flex-col">
      <ProfileNavbar
        username={username}
        showQueue={showQueueTab}
        showCrates={showCratesTab}
        activeTab={activeTab}
        onTabChange={setActiveTab}
      />

      <main className="flex-1 max-w-3xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-8">
        {/* Profile header */}
        {profile && (
          <div className="mb-4 mt-10 pb-6 border-b border-[var(--color-border)]">
            <div className="flex items-end justify-between gap-4">
              <div>
                <h1 className="font-serif text-3xl sm:text-4xl font-normal text-[var(--color-text-primary)] leading-tight">
                  {profile.full_name || `@${username}`}
                </h1>
              </div>
              <div className="text-right flex-shrink-0">
                {showQueueTab && (
                  <div className="font-mono text-[10px] text-[var(--color-text-faint)] uppercase tracking-widest">
                    <span className="text-[var(--color-text-primary)] font-serif text-xl">
                      {queue.length}
                    </span>{" "}
                    <span>article{queue.length !== 1 ? "s" : ""}</span>
                  </div>
                )}
                {showCratesTab && (
                  <div className="font-mono text-[10px] text-[var(--color-text-faint)] uppercase tracking-widest mt-0.5">
                    <span className="text-[var(--color-text-primary)] font-serif text-xl">
                      {vinyl.length}
                    </span>{" "}
                    <span>record{vinyl.length !== 1 ? "s" : ""}</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Filter row: view mode toggle */}
        <div className="flex items-baseline gap-4 text-xs text-[var(--color-text-faint)] uppercase tracking-wider relative z-20">
          {/* View mode toggle (queue only) */}
          {activeTab === "queue" && showQueueTab && (
            <div className="hidden sm:flex items-center gap-1.5 ml-auto">
              <button
                onClick={() => {
                  setViewMode("list");
                  localStorage.setItem("contentListViewMode", "list");
                }}
                title="List view"
                className={`p-0.5 transition-colors ${viewMode === "list" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <rect x="2" y="2" width="10" height="3" />
                  <rect x="2" y="7" width="10" height="3" />
                </svg>
              </button>
              <button
                onClick={() => {
                  setViewMode("index");
                  localStorage.setItem("contentListViewMode", "index");
                }}
                title="Index view"
                className={`p-0.5 transition-colors ${viewMode === "index" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
              >
                <svg
                  className="mb-[1px]"
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <line x1="2" y1="3" x2="12" y2="3" />
                  <line x1="2" y1="7" x2="12" y2="7" />
                  <line x1="2" y1="11" x2="12" y2="11" />
                </svg>
              </button>
            </div>
          )}
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex justify-center py-12">
            <RetroLoader
              text="Loading profile"
              className="text-sm text-[var(--color-accent)]"
            />
          </div>
        ) : (
          <>
            {activeTab === "queue" && showQueueTab && (
              <>
                {queue.length === 0 ? (
                  <div className="text-center py-16 text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] text-sm">
                    No public articles in their queue.
                  </div>
                ) : viewMode === "index" ? (
                  <div className="w-full">
                    <div
                      className="py-2 px-1 border-b border-[var(--color-text-primary)] font-serif text-[13px] text-[var(--color-text-primary)] sticky top-12 bg-[var(--color-bg-primary)] z-10 mb-2 whitespace-nowrap"
                      style={{
                        display: "grid",
                        gridTemplateColumns: "7rem 1fr 12rem auto",
                        gap: "0 1.5rem",
                      }}
                    >
                      <div>Date</div>
                      <div>Title</div>
                      <div>Author</div>
                      <div>Source</div>
                    </div>
                    {queue.map((item) => (
                      <ContentIndexItem
                        key={item.id}
                        content={item}
                        onStatusChange={noop as never}
                        onDelete={noopId}
                        readOnly
                        navigateTo={`/${username}/content/${item.id}`}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="divide-y divide-[var(--color-border-subtle)]">
                    {queue.map((item) => (
                      <ContentItem
                        key={item.id}
                        content={item}
                        onStatusChange={noop as never}
                        onDelete={noopId}
                        readOnly
                        navigateTo={`/${username}/content/${item.id}`}
                      />
                    ))}
                  </div>
                )}
              </>
            )}

            {activeTab === "crates" && showCratesTab && (
              <>
                {vinyl.length === 0 ? (
                  <div className="text-center py-16 text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] text-sm">
                    No records in their crates.
                  </div>
                ) : (
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
                    {vinyl.map((record) => (
                      <VinylCard
                        key={record.id}
                        record={record}
                        onClick={() => {}}
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
