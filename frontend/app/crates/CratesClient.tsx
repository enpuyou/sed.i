"use client";

import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  Fragment,
} from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import AddRecordForm from "@/components/AddRecordForm";
import VinylCard from "@/components/VinylCard";
import RecordDetail from "@/components/RecordDetail";
import RetroLoader from "@/components/RetroLoader";
import KeyboardShortcuts from "@/components/KeyboardShortcuts";
import ListeningMode from "@/components/ListeningMode";
import { vinylAPI } from "@/lib/api";
import { usePlayer } from "@/contexts/PlayerContext";
import { useReadingSettings } from "@/contexts/ReadingSettingsContext";
import { VinylRecord } from "@/types";
import EmptyState from "@/components/EmptyState";

type StatusFilter = "all" | "collection" | "wantlist" | "library";

export default function CratesClient() {
  const { settings } = useReadingSettings();
  const [records, setRecords] = useState<VinylRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [selectedRecord, setSelectedRecord] = useState<VinylRecord | null>(
    null,
  );
  const [sortBy, setSortBy] = useState<"recent" | "artist" | "year">("recent");
  const [search, setSearch] = useState("");
  const [density, setDensity] = useState<"loose" | "tight">("loose");
  const [lastDug, setLastDug] = useState<VinylRecord | null>(null);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [listenMode, setListenMode] = useState(false);
  const [visibleCount, setVisibleCount] = useState(18); // ~3 rows of 6
  const { current: playerCurrent, isPlaying: playerIsPlaying } = usePlayer();
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Load "now digging" from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem("now-digging");
      if (stored) setLastDug(JSON.parse(stored));
    } catch {
      /* ignore */
    }
  }, []);

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      const inInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

      if (e.key === "?" && !inInput) {
        e.preventDefault();
        setShowShortcuts((v) => !v);
        return;
      }
      if (e.key === "Escape") {
        if (listenMode) {
          setListenMode(false);
          return;
        }
        if (showShortcuts) {
          setShowShortcuts(false);
          return;
        }
        if (search) {
          setSearch("");
          return;
        }
        return;
      }
      if (inInput) return;
      if (e.key === "l") {
        if (playerCurrent || selectedRecord || lastDug) {
          setListenMode((v) => !v);
        }
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        const el = document.querySelector<HTMLInputElement>(
          "[data-crate-search]",
        );
        el?.focus();
      }
      if (e.key === "1") setSortBy("recent");
      if (e.key === "2") setSortBy("artist");
      if (e.key === "3") setSortBy("year");
      if (e.key === "d") setDensity((d) => (d === "loose" ? "tight" : "loose"));
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    showShortcuts,
    search,
    listenMode,
    playerCurrent,
    selectedRecord,
    lastDug,
  ]);

  const fetchRecords = useCallback(async () => {
    try {
      const params: { status?: string } = {};
      if (filter !== "all") params.status = filter;
      const data = await vinylAPI.getAll(params);
      setRecords(data);
    } catch {
      // silently fail — user sees empty state
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    setLoading(true);
    fetchRecords();
  }, [fetchRecords]);

  // Poll for pending records until metadata arrives
  useEffect(() => {
    const hasPending = records.some((r) => r.processing_status === "pending");
    if (!hasPending) return;
    const interval = setInterval(fetchRecords, 3000);
    return () => clearInterval(interval);
  }, [records, fetchRecords]);

  // Reset visible count when filters/sort/search change
  useEffect(() => {
    setVisibleCount(18);
  }, [filter, sortBy, search, density]);

  const handleRecordAdded = (newRecord: VinylRecord) => {
    setRecords((prev) => [newRecord, ...prev]);
  };

  const handleRecordDeleted = (id: string) => {
    setRecords((prev) => prev.filter((r) => r.id !== id));
    setSelectedRecord(null);
  };

  const filteredRecords = useMemo(() => {
    if (!search.trim()) return records;
    const q = search.toLowerCase();
    return records.filter((r) =>
      [
        r.artist,
        r.title,
        r.label,
        ...(r.genres || []),
        ...(r.styles || []),
        ...(r.tags || []),
      ]
        .filter(Boolean)
        .some((field) => field!.toLowerCase().includes(q)),
    );
  }, [records, search]);

  const sortedRecords = [...filteredRecords].sort((a, b) => {
    if (sortBy === "artist") {
      return (a.artist || "").localeCompare(b.artist || "");
    }
    if (sortBy === "year") {
      const yearA = a.year || 0;
      const yearB = b.year || 0;
      if (yearA !== yearB) return yearB - yearA;
      return (a.artist || "").localeCompare(b.artist || "");
    }
    // Default: recent (created_at descending)
    return b.created_at.localeCompare(a.created_at);
  });

  const PaginatedRecords = sortedRecords.slice(0, visibleCount);
  const hasMore = visibleCount < sortedRecords.length;

  // Infinite scroll — load 18 more when sentinel enters viewport
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && hasMore) {
          setVisibleCount((v) => v + 18);
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore]);

  const handleRecordUpdated = (updated: VinylRecord) => {
    setRecords((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
    setSelectedRecord(updated);
  };

  // Group by first letter of artist (for alphabet dividers)
  const groupedByLetter = useMemo(() => {
    if (sortBy !== "artist") return null;
    const groups: { letter: string; records: VinylRecord[] }[] = [];
    let currentLetter = "";
    for (const r of PaginatedRecords) {
      const letter = (r.artist || "#")[0].toUpperCase();
      const normalized = /[A-Z]/.test(letter) ? letter : "#";
      if (normalized !== currentLetter) {
        currentLetter = normalized;
        groups.push({ letter: normalized, records: [r] });
      } else {
        groups[groups.length - 1].records.push(r);
      }
    }
    return groups;
  }, [sortBy, PaginatedRecords]);

  const filters: { value: StatusFilter; label: string }[] = [
    { value: "all", label: "All" },
    { value: "collection", label: "Collection" },
    { value: "wantlist", label: "Wantlist" },
    { value: "library", label: "Library" },
  ];

  if (!settings.showCrates) {
    return (
      <div className="min-h-screen bg-[var(--color-bg-primary)]">
        <Navbar />
        <main className="max-w-3xl mx-auto px-6 py-16">
          <div className="border border-[var(--color-border)] p-8 text-center space-y-4">
            <h1 className="font-serif text-2xl text-[var(--color-text-primary)]">
              Crates disabled
            </h1>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Enable Crates + audio player in Settings → Feature Visibility.
            </p>
            <Link
              href="/settings"
              className="inline-block text-xs px-3 py-1 rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors no-underline"
              style={{ color: "var(--color-text-primary)" }}
            >
              Open settings
            </Link>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      <Navbar />

      <main className="max-w-none w-full px-5 sm:px-6 lg:px-8 py-8">
        <div className="max-w-none w-full">
          <div className="mb-8">
            <div className="flex items-baseline gap-4 mb-4">
              <h1 className="font-serif text-2xl font-normal text-[var(--color-text-primary)]">
                Crates
              </h1>
              <span className="font-mono text-[10px] text-[var(--color-text-faint)] tracking-wider">
                {filteredRecords.length}
                {search ? ` / ${records.length}` : ""}{" "}
                {records.length === 1 ? "record" : "records"}
              </span>
            </div>
            <div className="max-w-md">
              <AddRecordForm onRecordAdded={handleRecordAdded} />
            </div>
          </div>

          {/* Controls Bar: Filters, Search & Sort */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-3">
            {/* Left: Filter tags */}
            <div className="flex items-center gap-2">
              {filters.map((f) => (
                <button
                  key={f.value}
                  onClick={() => setFilter(f.value)}
                  className={`compact-touch text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 border transition-colors ${
                    filter === f.value
                      ? "border-[var(--color-accent)] text-[var(--color-text-primary)]"
                      : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-accent)]"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            {/* Right: Search + Sort */}
            <div className="flex items-center gap-6">
              {/* Search */}
              <div className="flex items-center gap-1.5 border-b border-[var(--color-border)] focus-within:border-[var(--color-text-muted)] transition-colors">
                <span className="text-[var(--color-text-faint)] text-xs select-none">
                  /
                </span>
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="search"
                  data-crate-search
                  className="bg-transparent border-none outline-none font-mono text-[10px] tracking-wider text-[var(--color-text-primary)] placeholder:text-[var(--color-text-faint)] w-24 focus:w-40 transition-all duration-200 py-0.5"
                />
                {search && (
                  <button
                    onClick={() => setSearch("")}
                    className="text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] text-xs leading-none"
                  >
                    &times;
                  </button>
                )}
              </div>

              {/* Density toggle — hidden on mobile */}
              <div className="hidden sm:flex items-center gap-1.5">
                <button
                  onClick={() => setDensity("loose")}
                  title="Loose grid"
                  className={`p-0.5 transition-colors ${density === "loose" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
                >
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  >
                    <rect x="1" y="1" width="5" height="5" />
                    <rect x="8" y="1" width="5" height="5" />
                    <rect x="1" y="8" width="5" height="5" />
                    <rect x="8" y="8" width="5" height="5" />
                  </svg>
                </button>
                <button
                  onClick={() => setDensity("tight")}
                  title="Tight grid"
                  className={`p-0.5 transition-colors ${density === "tight" ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"}`}
                >
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  >
                    <rect x="1" y="1" width="3" height="3" />
                    <rect x="5.5" y="1" width="3" height="3" />
                    <rect x="10" y="1" width="3" height="3" />
                    <rect x="1" y="5.5" width="3" height="3" />
                    <rect x="5.5" y="5.5" width="3" height="3" />
                    <rect x="10" y="5.5" width="3" height="3" />
                    <rect x="1" y="10" width="3" height="3" />
                    <rect x="5.5" y="10" width="3" height="3" />
                    <rect x="10" y="10" width="3" height="3" />
                  </svg>
                </button>
              </div>

              {/* Listen mode toggle — desktop only; mobile uses the Now Digging bar button */}
              {playerCurrent && (
                <button
                  onClick={() => setListenMode(true)}
                  className="hidden sm:block text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
                  title="Listening mode (l)"
                >
                  Listen
                </button>
              )}

              {/* Sort options */}
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-text-faint)]">
                  Sort /
                </span>
                {[
                  { id: "recent", label: "Added" },
                  { id: "artist", label: "Artist" },
                  { id: "year", label: "Year" },
                ].map((s) => (
                  <button
                    key={s.id}
                    onClick={() =>
                      setSortBy(s.id as "recent" | "artist" | "year")
                    }
                    className={`text-[10px] font-mono uppercase tracking-wider transition-colors ${
                      sortBy === s.id
                        ? "text-[var(--color-text-primary)] underline underline-offset-4"
                        : "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Now Digging / Now Listening bar */}
          {(lastDug || playerCurrent) &&
            !selectedRecord &&
            (() => {
              // Only show the playing track when music is *actively* playing.
              // When paused (or nothing queued), fall back to lastDug.
              const activeTrack =
                playerIsPlaying && playerCurrent ? playerCurrent : null;
              const coverUrl = activeTrack?.cover_url ?? lastDug?.cover_url;
              const artist = activeTrack?.artist ?? lastDug?.artist;
              const title = activeTrack?.title ?? lastDug?.title;
              const label = activeTrack ? "Now listening" : "Now digging";

              return (
                <button
                  onClick={() =>
                    activeTrack
                      ? setListenMode(true)
                      : lastDug && setSelectedRecord(lastDug)
                  }
                  className="w-full flex items-center gap-3 mb-4 py-1.5 px-2 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors cursor-pointer text-left"
                >
                  {coverUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={coverUrl}
                      alt=""
                      className="w-8 h-8 object-cover border border-[var(--color-border)] flex-shrink-0"
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <span className="font-mono text-[9px] uppercase tracking-widest text-[var(--color-text-faint)]">
                      {label}
                    </span>
                    <p className="text-[11px] text-[var(--color-text-primary)] truncate leading-none">
                      {artist} —{" "}
                      <span className="font-serif italic">{title}</span>
                    </p>
                  </div>
                </button>
              );
            })()}

          {/* Grid */}
          {loading ? (
            <div className="flex justify-center py-16">
              <RetroLoader text="Loading records" />
            </div>
          ) : records.length === 0 ? (
            <EmptyState
              message="No records yet."
              description="Paste a Discogs URL above to start digging."
              className="py-16"
            />
          ) : sortedRecords.length === 0 ? (
            <EmptyState
              message={`No records match \u201c${search}\u201d`}
              className="py-16"
            />
          ) : groupedByLetter ? (
            /* Alphabet-divided grid (artist sort) */
            groupedByLetter.map((group) => (
              <Fragment key={group.letter}>
                <div className="flex items-center gap-3 mt-6 mb-2 first:mt-0">
                  <span className="font-mono text-[11px] text-[var(--color-text-faint)] tracking-widest select-none">
                    {group.letter}
                  </span>
                  <div className="flex-1 h-px bg-[var(--color-border)]" />
                </div>
                <div
                  className={
                    density === "tight"
                      ? "grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10 gap-1"
                      : "grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4"
                  }
                >
                  {group.records.map((record) => (
                    <VinylCard
                      key={record.id}
                      record={record}
                      compact={density === "tight"}
                      onClick={() => {
                        setSelectedRecord(record);
                        setLastDug(record);
                        try {
                          localStorage.setItem(
                            "now-digging",
                            JSON.stringify(record),
                          );
                        } catch {}
                      }}
                    />
                  ))}
                </div>
              </Fragment>
            ))
          ) : (
            <div
              className={
                density === "tight"
                  ? "grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10 gap-1"
                  : "grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4"
              }
            >
              {PaginatedRecords.map((record) => (
                <VinylCard
                  key={record.id}
                  record={record}
                  compact={density === "tight"}
                  onClick={() => {
                    setSelectedRecord(record);
                    setLastDug(record);
                    try {
                      localStorage.setItem(
                        "now-digging",
                        JSON.stringify(record),
                      );
                    } catch {}
                  }}
                />
              ))}
            </div>
          )}

          {/* Infinite scroll sentinel */}
          {hasMore && <div ref={sentinelRef} className="h-1" />}
        </div>
      </main>

      {/* Detail overlay */}
      <RecordDetail
        record={selectedRecord}
        isOpen={!!selectedRecord}
        onClose={() => setSelectedRecord(null)}
        onDelete={handleRecordDeleted}
        onUpdate={handleRecordUpdated}
      />

      {/* Keyboard shortcuts overlay */}
      <KeyboardShortcuts
        isOpen={showShortcuts}
        onClose={() => setShowShortcuts(false)}
      />

      {/* Listening mode overlay */}
      <ListeningMode
        isOpen={listenMode}
        onClose={() => setListenMode(false)}
        record={selectedRecord || lastDug}
      />
    </div>
  );
}
