"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { VinylRecord } from "@/types";
import { vinylAPI } from "@/lib/api";
import { usePlayer, QueueTrack } from "@/contexts/PlayerContext";
import InlineError from "./InlineError";

interface RecordDetailProps {
  record: VinylRecord | null;
  isOpen: boolean;
  onClose: () => void;
  onDelete: (id: string) => void;
  onUpdate: (record: VinylRecord) => void;
}

export default function RecordDetail({
  record,
  isOpen,
  onClose,
  onDelete,
  onUpdate,
}: RecordDetailProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [visible, setVisible] = useState(false);
  const [editingCover, setEditingCover] = useState(false);
  const [coverInput, setCoverInput] = useState("");
  const [editingStyles, setEditingStyles] = useState(false);
  const [stylesInput, setStylesInput] = useState("");
  const [addingVideo, setAddingVideo] = useState(false);
  const [videoInput, setVideoInput] = useState("");
  const { addToQueue, addMultipleToQueue, play, queue, currentIndex } =
    usePlayer();
  const [justQueued, setJustQueued] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Animate in/out + lock body scroll
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
      requestAnimationFrame(() => setVisible(true));
    } else {
      document.body.style.overflow = "";
      setVisible(false);
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  // Reset edit states when record changes
  useEffect(() => {
    setEditingCover(false);
    setEditingStyles(false);
    setAddingVideo(false);
    setConfirmDelete(false);
    setActionError(null);
  }, [record?.id]);

  // Keyboard: Escape to close
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setConfirmDelete(false);
        setEditingCover(false);
        setEditingStyles(false);
        setAddingVideo(false);
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  // Backfill: when the player learns a track's duration, persist it to the backend
  const backfilledRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!record) return;
    const currentTrack = currentIndex >= 0 ? queue[currentIndex] : null;
    if (!currentTrack || currentTrack.recordId !== record.id) return;
    if (!currentTrack.duration || currentTrack.duration === 0) return;
    if (backfilledRef.current.has(currentTrack.videoId)) return;

    // Check if the local video list is missing this duration
    const localVideo = record.videos?.find((v) =>
      v.uri.includes(currentTrack.videoId),
    );
    if (localVideo && (!localVideo.duration || localVideo.duration === 0)) {
      backfilledRef.current.add(currentTrack.videoId);

      // For API: prefer undefined
      const updatedVideosForApi = (record.videos || []).map((v) => ({
        ...v,
        title: v.title ?? undefined,
        duration: v.uri.includes(currentTrack.videoId)
          ? (currentTrack.duration ?? undefined)
          : (v.duration ?? undefined),
      }));

      // For Local State: prefer null (matches VinylVideo type)
      const updatedVideosForState = (record.videos || []).map((v) => ({
        ...v,
        duration: v.uri.includes(currentTrack.videoId)
          ? (currentTrack.duration ?? null)
          : v.duration,
      }));

      // Update backend silently
      vinylAPI
        .update(record.id, { videos: updatedVideosForApi })
        .catch(() => {});
      // Update local state so the UI reflects it immediately
      onUpdate({ ...record, videos: updatedVideosForState });
    }
  }, [queue, currentIndex, record, onUpdate]);

  if (!isOpen || !record) return null;

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    try {
      setActionError(null);
      await vinylAPI.delete(record.id);
      onDelete(record.id);
    } catch {
      setActionError("Couldn't delete record. Try again.");
    }
    setConfirmDelete(false);
  };

  const handleStatusToggle = async () => {
    const newStatus = record.status === "wantlist" ? "collection" : "wantlist";
    try {
      setActionError(null);
      const updated = await vinylAPI.update(record.id, {
        status: newStatus,
      });
      onUpdate(updated);
    } catch {
      setActionError("Couldn't update status. Try again.");
    }
  };

  const handleCoverUpdate = async () => {
    if (!coverInput.trim()) return;
    try {
      setActionError(null);
      const updated = await vinylAPI.update(record.id, {
        cover_url: coverInput.trim(),
      });
      onUpdate(updated);
      setEditingCover(false);
      setCoverInput("");
    } catch {
      setActionError("Couldn't update cover. Try again.");
    }
  };

  const handleStylesUpdate = async () => {
    const parts = stylesInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      setActionError(null);
      const updated = await vinylAPI.update(record.id, {
        styles: parts,
      });
      onUpdate(updated);
      setEditingStyles(false);
    } catch {
      setActionError("Couldn't update styles. Try again.");
    }
  };

  const handleAddVideo = async () => {
    if (!videoInput.trim()) return;
    const newVideo = { title: "", uri: videoInput.trim() };
    const existingVideos = (record.videos || []).map((v) => ({
      title: v.title || "",
      uri: v.uri,
      duration: v.duration ?? undefined,
    }));
    if (existingVideos.some((v) => v.uri === newVideo.uri)) {
      setAddingVideo(false);
      setVideoInput("");
      return;
    }
    try {
      setActionError(null);
      const updated = await vinylAPI.update(record.id, {
        videos: [...existingVideos, newVideo],
      });
      onUpdate(updated);
      setAddingVideo(false);
      setVideoInput("");
    } catch {
      setActionError("Couldn't add video. Try again.");
    }
  };

  const handleRemoveVideo = async (uri: string) => {
    const filtered = (record.videos || [])
      .filter((v) => v.uri !== uri)
      .map((v) => ({
        title: v.title || "",
        uri: v.uri,
        duration: v.duration ?? undefined,
      }));
    try {
      setActionError(null);
      const updated = await vinylAPI.update(record.id, { videos: filtered });
      onUpdate(updated);
    } catch {
      setActionError("Couldn't remove video. Try again.");
    }
  };

  // Group tracklist by side (A/B)
  const sides: Record<string, typeof record.tracklist> = {};
  for (const track of record.tracklist || []) {
    const side = track.position?.[0]?.toUpperCase() || "?";
    if (!sides[side]) sides[side] = [];
    sides[side].push(track);
  }

  const allGenres = record.genres || [];
  const allStyles = record.styles || [];

  // Deduplicate videos by URI (Discogs can return duplicates)
  const uniqueVideos = (record.videos || []).filter(
    (v, i, arr) => arr.findIndex((x) => x.uri === v.uri) === i,
  );

  // Extract YouTube video ID from various URL formats
  const extractVideoId = (uri: string): string | null => {
    const match = uri.match(
      /(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
    );
    return match ? match[1] : null;
  };

  const youtubeVideos = uniqueVideos
    .map((v) => ({ ...v, videoId: extractVideoId(v.uri) }))
    .filter((v): v is typeof v & { videoId: string } => v.videoId !== null);

  const handlePlayAll = () => {
    const tracks: QueueTrack[] = youtubeVideos.map((v) => ({
      videoId: v.videoId,
      title: v.title || v.videoId,
      artist: record.artist || undefined,
      album: record.title || undefined,
      cover_url: record.cover_url || undefined,
      duration: v.duration ?? undefined,
      recordId: record.id,
    }));
    addMultipleToQueue(tracks);
    play(queue.length); // play first of the newly added batch
  };

  const handleQueueTrack = (v: {
    videoId: string;
    title: string | null;
    uri: string;
    duration?: number;
  }) => {
    addToQueue({
      videoId: v.videoId,
      title: v.title || v.videoId,
      artist: record.artist || undefined,
      album: record.title || undefined,
      cover_url: record.cover_url || undefined,
      duration: v.duration,
      recordId: record.id,
    });
    setJustQueued(v.videoId);
    setTimeout(() => setJustQueued(null), 1200);
  };

  const handlePlayTrack = (v: {
    videoId: string;
    title: string | null;
    uri: string;
    duration?: number;
  }) => {
    addToQueue({
      videoId: v.videoId,
      title: v.title || v.videoId,
      artist: record.artist || undefined,
      album: record.title || undefined,
      cover_url: record.cover_url || undefined,
      duration: v.duration,
      recordId: record.id,
    });
    // The new track is at the end of the queue
    play(queue.length);
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 ${visible ? "opacity-100" : "opacity-0"}`}
        onClick={() => {
          setConfirmDelete(false);
          setEditingCover(false);
          setEditingStyles(false);
          setAddingVideo(false);
          onClose();
        }}
      />

      {/* Gatefold panel */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div
          className={`pointer-events-auto bg-[var(--color-bg-primary)] border border-[var(--color-border)] overflow-hidden flex flex-col md:flex-row md:aspect-[2/1] transition-all duration-200 ${visible ? "opacity-100 scale-100" : "opacity-0 scale-[0.97]"}`}
          style={{ width: "min(85vw, 75vh * 2)", maxHeight: "80vh" }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Left panel — Cover art */}
          <div className="relative w-full md:w-1/2 md:h-full aspect-square md:aspect-auto flex-shrink-0 bg-[var(--color-bg-tertiary)] border-b md:border-b-0 md:border-r border-[var(--color-border)] group/cover">
            {record.cover_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={record.cover_url}
                alt={`${record.artist} — ${record.title}`}
                className="w-full h-full object-cover"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                <span className="font-serif text-5xl text-[var(--color-text-faint)] tracking-wide">
                  {(record.artist?.[0] || "?") + (record.title?.[0] || "?")}
                </span>
              </div>
            )}

            {/* Edit cover button — appears on hover */}
            <button
              onClick={() => {
                setCoverInput(record.cover_url || "");
                setEditingCover(true);
              }}
              className="absolute bottom-2 right-2 font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 bg-black/60 text-white/70 hover:text-white opacity-0 group-hover/cover:opacity-100 transition-opacity"
            >
              edit cover
            </button>

            {/* Cover URL editor overlay */}
            {editingCover && (
              <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center p-6 gap-3">
                <p className="font-mono text-[10px] uppercase tracking-wider text-white/60">
                  Paste image URL
                </p>
                <input
                  type="url"
                  value={coverInput}
                  onChange={(e) => setCoverInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleCoverUpdate();
                    }
                    if (e.key === "Escape") {
                      e.stopPropagation();
                      setEditingCover(false);
                    }
                  }}
                  placeholder="https://..."
                  autoFocus
                  className="w-full max-w-sm bg-transparent border-b border-white/30 focus:border-white text-white text-sm font-mono outline-none py-1 placeholder:text-white/30"
                />
                <div className="flex gap-3 mt-2">
                  <button
                    onClick={handleCoverUpdate}
                    className="font-mono text-[10px] uppercase tracking-wider px-3 py-1 border border-white/40 text-white hover:bg-white/10 transition-colors"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditingCover(false)}
                    className="font-mono text-[10px] uppercase tracking-wider px-3 py-1 text-white/50 hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Right panel — Details */}
          <div className="relative w-full md:w-1/2 md:h-full overflow-y-auto p-4 flex flex-col">
            {/* Close button */}
            <button
              onClick={onClose}
              className="absolute top-3 right-3 w-7 h-7 flex items-center justify-center border border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-muted)] hover:border-red-400 hover:text-red-400 transition-colors z-10"
            >
              <span className="leading-none text-[16px] pb-0.5">&times;</span>
            </button>

            {/* Artist */}
            <p className="font-sans text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
              {record.artist || "Unknown Artist"}
            </p>

            {/* Title */}
            <h2 className="font-serif text-[22px] font-normal text-[var(--color-text-primary)] leading-tight tracking-[-0.01em] mt-1 mb-2.5">
              {record.title || "Untitled"}
            </h2>

            {/* Metadata — compact mono block */}
            <div className="font-mono text-[10px] text-[var(--color-text-muted)] tracking-wide leading-relaxed">
              {[record.label, record.year].filter(Boolean).join(" · ")}
            </div>

            {/* Genres + Styles */}
            {(allGenres.length > 0 || allStyles.length > 0) && (
              <div className="mt-2.5">
                <div className="flex flex-wrap gap-1.5 items-center">
                  {allGenres.map((g) => (
                    <span
                      key={`g-${g}`}
                      className="font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1.5 py-0.5"
                    >
                      {g}
                    </span>
                  ))}
                  {allStyles.map((s) => (
                    <span
                      key={`s-${s}`}
                      className="font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--color-text-faint)] border border-[var(--color-border)] border-dashed px-1.5 py-0.5"
                    >
                      {s}
                    </span>
                  ))}
                  <button
                    onClick={() => {
                      setStylesInput(allStyles.join(", "));
                      setEditingStyles(true);
                    }}
                    className="font-mono text-[9px] text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors px-0.5"
                  >
                    +
                  </button>
                </div>
                {editingStyles && (
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="text"
                      value={stylesInput}
                      onChange={(e) => setStylesInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          handleStylesUpdate();
                        }
                        if (e.key === "Escape") {
                          e.stopPropagation();
                          setEditingStyles(false);
                        }
                      }}
                      placeholder="house, techno, ambient..."
                      autoFocus
                      className="flex-1 bg-transparent border-b border-[var(--color-border)] focus:border-[var(--color-text-muted)] font-mono text-[10px] text-[var(--color-text-primary)] outline-none py-0.5 placeholder:text-[var(--color-text-faint)]"
                    />
                    <button
                      onClick={handleStylesUpdate}
                      className="font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                    >
                      save
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Tracklist */}
            {Object.keys(sides).length > 0 && (
              <div className="border-t border-[var(--color-border)] pt-3 mt-4">
                <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-text-muted)] mb-2">
                  Tracklist
                </p>
                {Object.entries(sides).map(([side, tracks], sideIndex) => (
                  <div key={side} className={sideIndex > 0 ? "mt-2" : ""}>
                    <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-faint)] mb-1">
                      Side {side}
                    </p>
                    {tracks.map((track, i) => (
                      <div
                        key={i}
                        className="flex items-baseline justify-between py-1.5 text-[13px] text-[var(--color-text-secondary)] border-b border-black/[0.04] last:border-b-0"
                      >
                        <div className="flex items-baseline gap-1.5 min-w-0">
                          <span className="font-mono text-[11px] text-[var(--color-text-faint)] w-7 flex-shrink-0">
                            {track.position}
                          </span>
                          <span className="truncate">{track.title}</span>
                        </div>
                        {track.duration && (
                          <span className="font-mono text-[11px] text-[var(--color-text-faint)] flex-shrink-0 ml-2">
                            {track.duration}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}

            {/* Videos / YouTube links */}
            <div className="border-t border-[var(--color-border)] pt-3 mt-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
                    Videos
                  </p>
                  <button
                    onClick={() => setAddingVideo(true)}
                    className="font-mono text-[10px] text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors"
                  >
                    + add
                  </button>
                </div>
                {youtubeVideos.length > 0 && (
                  <button
                    onClick={handlePlayAll}
                    className="font-mono text-[10px] text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
                  >
                    &#9654; play all
                  </button>
                )}
              </div>
              {uniqueVideos.map((video) => {
                const vid = extractVideoId(video.uri);
                return (
                  <div
                    key={video.uri}
                    className="flex flex-nowrap items-center gap-1.5 sm:gap-2 py-0.5 sm:py-1 group/video"
                  >
                    <div className="flex-1 min-w-0 overflow-hidden">
                      <a
                        href={video.uri}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline font-mono text-[11px] sm:text-[12px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] no-underline transition-colors"
                      >
                        {video.title ||
                          video.uri.replace(
                            /https?:\/\/(www\.)?youtube\.com\/watch\?v=/,
                            "",
                          )}
                      </a>
                    </div>
                    {vid && (
                      <>
                        <button
                          onClick={() =>
                            handlePlayTrack({
                              videoId: vid,
                              title: video.title ?? null,
                              uri: video.uri,
                              duration: video.duration ?? undefined,
                            })
                          }
                          className="compact-touch font-mono text-[11px] text-[var(--color-text-faint)] hover:text-[var(--color-accent)] sm:opacity-0 sm:group-hover/video:opacity-100 transition-opacity flex-shrink-0"
                          title="Play now"
                        >
                          &#9654;
                        </button>
                        <button
                          onClick={() =>
                            handleQueueTrack({
                              videoId: vid,
                              title: video.title ?? null,
                              uri: video.uri,
                              duration: video.duration ?? undefined,
                            })
                          }
                          className={`compact-touch font-mono text-[11px] flex-shrink-0 transition-all ${
                            justQueued === vid
                              ? "text-[var(--color-accent)] opacity-100"
                              : "text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] sm:opacity-0 sm:group-hover/video:opacity-100"
                          }`}
                          title="Add to queue"
                        >
                          {justQueued === vid ? "ok" : "+Q"}
                        </button>
                      </>
                    )}
                    {video.duration && video.duration > 0 && (
                      <span className="font-mono text-[10px] sm:text-[11px] text-[var(--color-text-faint)] flex-shrink-0">
                        {Math.floor(video.duration / 60)}:
                        {String(video.duration % 60).padStart(2, "0")}
                      </span>
                    )}
                    <button
                      onClick={() => handleRemoveVideo(video.uri)}
                      className="compact-touch font-mono text-[11px] text-[var(--color-text-faint)] hover:text-red-400 sm:opacity-0 sm:group-hover/video:opacity-100 transition-opacity flex-shrink-0"
                    >
                      &times;
                    </button>
                  </div>
                );
              })}
              {uniqueVideos.length === 0 && !addingVideo && (
                <p className="font-mono text-[10px] text-[var(--color-text-faint)] italic">
                  No videos yet
                </p>
              )}
              {addingVideo && (
                <div className="flex items-center gap-2 mt-1">
                  <input
                    type="url"
                    value={videoInput}
                    onChange={(e) => setVideoInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddVideo();
                      }
                      if (e.key === "Escape") {
                        e.stopPropagation();
                        setAddingVideo(false);
                        setVideoInput("");
                      }
                    }}
                    placeholder="YouTube URL..."
                    autoFocus
                    className="flex-1 bg-transparent border-b border-[var(--color-border)] focus:border-[var(--color-text-muted)] font-mono text-[11px] text-[var(--color-text-primary)] outline-none py-0.5 placeholder:text-[var(--color-text-faint)]"
                  />
                  <button
                    onClick={handleAddVideo}
                    className="font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                  >
                    add
                  </button>
                </div>
              )}
            </div>

            {/* Notes */}
            {record.notes && (
              <div className="border-t border-[var(--color-border)] pt-3 mt-4">
                <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--color-text-muted)] mb-2">
                  Notes
                </p>
                <p className="text-[13px] text-[var(--color-text-secondary)] leading-relaxed whitespace-pre-wrap italic">
                  {record.notes}
                </p>
              </div>
            )}

            {/* Tags */}
            {record.tags?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-3">
                {record.tags.map((tag) => (
                  <span
                    key={tag}
                    className="font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1.5 py-0.5"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Spacer */}
            <div className="flex-1 min-h-4" />

            {/* Action Error */}
            {actionError && (
              <InlineError
                message={actionError}
                onDismiss={() => setActionError(null)}
                className="mt-3 py-1.5"
              />
            )}

            {/* Actions */}
            <div className="flex items-center gap-2 pt-3 mt-4 border-t border-[var(--color-border)]">
              <button
                onClick={handleStatusToggle}
                className="compact-touch font-mono text-[10px] uppercase tracking-wider px-2 py-1 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-text-primary)] hover:text-[var(--color-text-primary)] transition-colors"
              >
                {record.status === "wantlist"
                  ? "\u2192 Collection"
                  : "\u2192 Wantlist"}
              </button>

              <a
                href={record.discogs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="compact-touch font-mono text-[10px] uppercase tracking-wider px-2 py-1 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-text-primary)] hover:text-[var(--color-text-primary)] transition-colors no-underline"
              >
                Discogs
              </a>

              <div className="flex-1" />

              <button
                onClick={handleDelete}
                className={`compact-touch font-mono text-[10px] uppercase tracking-wider px-2 py-1 border transition-colors ${
                  confirmDelete
                    ? "border-red-400 text-red-400"
                    : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-red-400 hover:text-red-400"
                }`}
              >
                {confirmDelete ? "Confirm?" : "Delete"}
              </button>
            </div>

            {/* Processing indicator */}
            {record.processing_status === "pending" && (
              <p className="font-mono text-[9px] text-[var(--color-text-faint)] animate-pulse mt-2">
                Fetching metadata from Discogs...
              </p>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
