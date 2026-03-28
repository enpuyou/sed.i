import { useState, useRef, useEffect } from "react";
import { usePlayer } from "@/contexts/PlayerContext";

export default function NowPlaying({
  direction = "down",
}: {
  direction?: "up" | "down";
}) {
  const {
    current,
    isPlaying,
    toggle,
    next,
    prev,
    queue,
    currentIndex,
    play,
    removeFromQueue,
    clearQueue,
    progress,
    duration,
    isBuffering,
  } = usePlayer();
  const [showQueue, setShowQueue] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [isMounted, setIsMounted] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  // Auto-rotate between info and progress every 8 seconds
  useEffect(() => {
    if (!current || !isPlaying) return;
    const interval = setInterval(() => {
      setShowProgress((prev) => !prev);
    }, 8000);
    return () => clearInterval(interval);
  }, [current, isPlaying]);

  // Reset to info when track changes
  useEffect(() => {
    setShowProgress(false);
  }, [current?.videoId]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setShowQueue(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const formatTime = (secs: number) => {
    if (!secs) return "0:00";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  // Avoid hydration mismatch by not rendering until mounted
  if (!isMounted) return null;

  // Empty state — show nothing
  if (!current && queue.length === 0) {
    return null;
  }

  const progressPercent = duration ? (progress / duration) * 100 : 0;
  const barWidth = 26;
  const filled = Math.round((progressPercent / 100) * barWidth);
  const progressBar =
    "\u2588".repeat(filled) + "\u2591".repeat(barWidth - filled);

  return (
    <div className="relative" ref={containerRef}>
      {/* Main player — group/player for hover detection */}
      <div className="flex items-center gap-1 px-1 h-10 group/player relative">
        {current && (
          <>
            {/* Album art — click toggles queue */}
            <button
              onClick={() => setShowQueue(!showQueue)}
              className="relative w-6 h-6 flex-shrink-0 overflow-hidden border border-[var(--color-border)] bg-[var(--color-bg-tertiary)] select-none hover:border-[var(--color-accent)] transition-colors"
            >
              {current.cover_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={current.cover_url}
                  alt=""
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <span className="font-mono text-[8px] text-[var(--color-text-faint)]">
                    ?
                  </span>
                </div>
              )}
            </button>

            {/* Info / Controls Container */}
            <div className="relative flex items-center w-52 h-4 overflow-visible ml-1">
              {/* DEFAULT: Rolling ticker (info ↔ progress) */}
              <div
                onClick={() => setShowQueue(!showQueue)}
                className="absolute inset-0 cursor-pointer opacity-100 group-hover/player:opacity-0 group-hover/player:pointer-events-none transition-opacity duration-200 player-ticker"
              >
                <div
                  className={`player-ticker-reel ${showProgress ? "show-progress" : ""}`}
                >
                  {/* Face 1: Song info */}
                  <div className="player-ticker-face">
                    <div
                      className={`overflow-hidden w-full ${isPlaying ? "mask-linear-fade" : ""}`}
                    >
                      <div
                        className={`whitespace-nowrap font-mono text-[12px] text-[var(--color-text-primary)] leading-none ${isPlaying ? "animate-scroll-text" : ""}`}
                      >
                        <span className="mr-8">
                          {current.title}
                          <span className="text-[var(--color-text-faint)] mx-1.5">
                            /
                          </span>
                          <span className="text-[var(--color-text-muted)]">
                            {current.artist || "?"}
                          </span>
                        </span>
                        {isPlaying && (
                          <span className="mr-8">
                            {current.title}
                            <span className="text-[var(--color-text-faint)] mx-1.5">
                              /
                            </span>
                            <span className="text-[var(--color-text-muted)]">
                              {current.artist || "?"}
                            </span>
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Face 2: Progress bar */}
                  <div className="player-ticker-face">
                    <div className="font-mono leading-none text-[var(--color-text-faint)] flex items-center whitespace-nowrap w-full">
                      <span className="text-[var(--color-text-muted)] tracking-[-0.08em] text-[14px] leading-none flex-1 overflow-hidden">
                        {progressBar}
                      </span>
                      <span className="tabular-nums text-[var(--color-text-muted)] text-[10px] flex-shrink-0 ml-1.5">
                        {formatTime(progress)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* HOVER: Transport controls — minimal geometric SVGs */}
              <div className="absolute inset-0 flex items-center justify-start pl-2 opacity-0 group-hover/player:opacity-100 transition-opacity duration-200 pointer-events-none group-hover/player:pointer-events-auto">
                <div className="flex items-center gap-3 select-none">
                  {/* Prev */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      prev();
                    }}
                    disabled={currentIndex <= 0}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] disabled:opacity-15 transition-colors p-0.5"
                    title="Previous"
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 10 10"
                      fill="currentColor"
                    >
                      <rect x="0" y="1" width="1.5" height="8" />
                      <polygon points="9,1 9,9 2.5,5" />
                    </svg>
                  </button>
                  {/* Play / Pause */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle();
                    }}
                    className="text-[var(--color-text-primary)] hover:text-[var(--color-accent)] transition-colors p-0.5"
                    title={isPlaying ? "Pause" : "Play"}
                  >
                    {isBuffering ? (
                      <span className="font-mono text-[10px] tracking-wider">
                        ..
                      </span>
                    ) : isPlaying ? (
                      <svg
                        width="10"
                        height="10"
                        viewBox="0 0 10 10"
                        fill="currentColor"
                      >
                        <rect x="1" y="1" width="2.5" height="8" />
                        <rect x="6.5" y="1" width="2.5" height="8" />
                      </svg>
                    ) : (
                      <svg
                        width="10"
                        height="10"
                        viewBox="0 0 10 10"
                        fill="currentColor"
                      >
                        <polygon points="2,1 2,9 9,5" />
                      </svg>
                    )}
                  </button>
                  {/* Next */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      next();
                    }}
                    disabled={currentIndex >= queue.length - 1}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] disabled:opacity-15 transition-colors p-0.5"
                    title="Next"
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 10 10"
                      fill="currentColor"
                    >
                      <polygon points="1,1 1,9 7.5,5" />
                      <rect x="8.5" y="1" width="1.5" height="8" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Queue Popover */}
      {showQueue && queue.length > 0 && (
        <div
          className={`absolute left-0 w-72 max-h-80 overflow-y-auto bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-lg z-50 ${direction === "up" ? "bottom-full mb-1" : "top-full mt-1"}`}
        >
          <div className="flex items-center justify-between px-2 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] sticky top-0 z-10">
            <span className="font-mono text-[9px] uppercase text-[var(--color-text-muted)] tracking-wider">
              queue [{queue.length - currentIndex}]
            </span>
            <button
              onClick={clearQueue}
              className="font-mono text-[9px] text-[var(--color-text-faint)] hover:text-rose-500 transition-colors"
            >
              clear
            </button>
          </div>

          <div className="py-0.5">
            {queue.map((track, i) => {
              const isPlayed = i < currentIndex;
              const isCurrent = i === currentIndex;
              return (
                <div
                  key={`${track.videoId}-${i}`}
                  className={`flex items-center gap-1.5 px-2 py-1 cursor-pointer hover:bg-[var(--color-bg-secondary)] group transition-colors ${isCurrent ? "bg-[var(--color-bg-secondary)]" : isPlayed ? "opacity-35" : ""}`}
                >
                  <button
                    onClick={() => play(i)}
                    className="flex-1 min-w-0 flex items-center gap-2 text-left"
                  >
                    <span
                      className={`font-mono text-[9px] w-3 text-right flex-shrink-0 ${isCurrent ? "text-[var(--color-accent)]" : "text-[var(--color-text-faint)]"}`}
                    >
                      {isCurrent && isPlaying ? "\u25B8" : i + 1}
                    </span>

                    <div className="w-4 h-4 bg-[var(--color-bg-tertiary)] flex-shrink-0 overflow-hidden border border-[var(--color-border)]">
                      {track.cover_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={track.cover_url}
                          alt=""
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <div className="w-full h-full bg-[var(--color-border)]" />
                      )}
                    </div>

                    <div className="flex-1 min-w-0">
                      <p
                        className={`font-mono text-[10px] leading-tight truncate ${isCurrent ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-secondary)]"}`}
                      >
                        {track.title}
                      </p>
                      <div className="flex items-center justify-between gap-1">
                        <p className="font-mono text-[9px] text-[var(--color-text-faint)] truncate">
                          {track.artist}
                        </p>
                        {track.duration && track.duration > 0 && (
                          <span className="font-mono text-[9px] text-[var(--color-text-faint)]">
                            {formatTime(track.duration)}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFromQueue(i);
                    }}
                    className="opacity-0 group-hover:opacity-100 font-mono text-[9px] text-[var(--color-text-faint)] hover:text-rose-500 transition-all px-0.5"
                    title="Remove"
                  >
                    x
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
