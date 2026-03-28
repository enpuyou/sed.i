"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import SearchBar from "@/components/SearchBar";
import ThemeToggle from "@/components/ThemeToggle";
import { usePathname } from "next/navigation";
import { SHOW_CRATES } from "@/lib/flags";
import NowPlaying from "@/components/NowPlaying";
import SediLogo from "@/components/SediLogo";
import { usePlayer } from "@/contexts/PlayerContext";

// Nav link that forces text-primary color (overrides global `a` color rule)
function NavLink({
  href,
  active,
  onClick,
  children,
}: {
  href: string;
  active: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      onClick={onClick}
      className={`compact-touch text-xs px-2 py-0.5 leading-none rounded-none border transition-colors no-underline ${
        active
          ? "bg-[var(--color-bg-secondary)] border-[var(--color-accent)]"
          : "bg-[var(--color-bg-secondary)] border-[var(--color-border)] hover:border-[var(--color-accent)]"
      }`}
      style={{ color: "var(--color-text-primary)" }}
    >
      {children}
    </Link>
  );
}

interface NavbarProps {
  // Writing mode: replaces nav links with writing controls
  writingMode?: boolean;
  onWritingClose?: () => void;
  onWritingExport?: (format?: "md" | "pdf" | "docx") => void;
  // Fullscreen/distraction-free: transparent bg, hide logo+search, controls only
  fullscreenMode?: boolean;
}

export default function Navbar({
  writingMode = false,
  onWritingClose,
  onWritingExport,
  fullscreenMode = false,
}: NavbarProps = {}) {
  const pathname = usePathname();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isVisible, setIsVisible] = useState(true);
  const [exportOpen, setExportOpen] = useState(false);
  const exportRef = useRef<HTMLDivElement>(null);
  const lastScrollY = useRef(0);
  const { current: playerCurrent, isPlaying, toggle } = usePlayer();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const isQueueActive = pathname === "/dashboard";
  const isListsActive = pathname === "/lists";
  const isSettingsActive = pathname === "/settings";
  const isCratesActive =
    pathname === "/crates" || pathname.startsWith("/crates/");

  // Scroll-based visibility
  // In fullscreen mode, listen on the editor's scroll container (not window)
  useEffect(() => {
    const SCROLL_THRESHOLD = 10;

    const getScrollTarget = () =>
      fullscreenMode
        ? (document.querySelector(
            ".typewriter-scroll-container",
          ) as HTMLElement | null)
        : null;

    const handleScroll = (e: Event) => {
      const target = e.target as HTMLElement;
      const scrollY = target ? target.scrollTop : window.scrollY;
      const deltaY = scrollY - lastScrollY.current;

      if (Math.abs(deltaY) > SCROLL_THRESHOLD) {
        if (deltaY > 0 && scrollY > 60) {
          setIsVisible(false);
        } else if (deltaY < 0 || scrollY < 30) {
          setIsVisible(true);
        }
        lastScrollY.current = scrollY;
      }
    };

    const handleWindowScroll = () => {
      const scrollY = window.scrollY;
      const deltaY = scrollY - lastScrollY.current;
      if (Math.abs(deltaY) > SCROLL_THRESHOLD) {
        if (deltaY > 0 && scrollY > 100) setIsVisible(false);
        else if (deltaY < 0 || scrollY < 50) setIsVisible(true);
        lastScrollY.current = scrollY;
      }
    };

    if (fullscreenMode) {
      // Defer to let the DOM render the scroll container
      const timer = setTimeout(() => {
        const container = getScrollTarget();
        if (container) {
          container.addEventListener("scroll", handleScroll, { passive: true });
        }
      }, 100);
      return () => {
        clearTimeout(timer);
        const container = getScrollTarget();
        if (container) container.removeEventListener("scroll", handleScroll);
      };
    } else {
      window.addEventListener("scroll", handleWindowScroll, { passive: true });
      return () => window.removeEventListener("scroll", handleWindowScroll);
    }
  }, [fullscreenMode]);

  useEffect(() => {
    if (!exportOpen) return;
    const handler = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [exportOpen]);

  const handleExport = useCallback(
    (format: "md" | "pdf" | "docx") => {
      setExportOpen(false);
      onWritingExport?.(format);
    },
    [onWritingExport],
  );

  const closeMobileMenu = () => setIsMobileMenuOpen(false);

  return (
    <nav
      className={`sticky top-0 z-50 w-full transition-transform duration-300 ${isVisible ? "translate-y-0" : "-translate-y-full"} ${fullscreenMode ? "bg-transparent border-b border-transparent" : ""}`}
    >
      <div className={fullscreenMode ? "px-3" : "px-4 sm:px-6 lg:px-8"}>
        <div
          className={`flex items-center justify-between w-full ${fullscreenMode ? "h-8" : "h-14"}`}
        >
          {/* Left: Logo & Player — hidden in fullscreen mode */}
          {!fullscreenMode && (
            <div className="flex items-center gap-4 w-1/4">
              <Link
                href="/dashboard"
                className="text-xl font-normal whitespace-nowrap flex items-center gap-2 shrink-0 no-underline hover:opacity-100"
                style={{
                  fontFamily: "var(--font-logo)",
                  color: "var(--color-text-primary)",
                }}
              >
                <SediLogo
                  size={20}
                  className="text-[var(--color-text-primary)]"
                />
                sed.i
              </Link>
              <div className="hidden md:block">
                <NowPlaying />
              </div>
            </div>
          )}
          {/* Fullscreen: spacer to push controls right */}
          {fullscreenMode && <div className="flex-1" />}

          {/* Center: Search — hidden in fullscreen mode */}
          {!fullscreenMode && (
            <div className="hidden md:flex flex-1 justify-center max-w-lg mx-4">
              <div className="w-full">
                <SearchBar />
              </div>
            </div>
          )}

          {/* Right: Writing controls OR Navigation Links */}
          <div className="hidden md:flex items-center justify-end gap-2 w-1/4">
            {writingMode ? (
              <>
                <ThemeToggle />
                {/* Export dropdown */}
                <div ref={exportRef} className="relative flex items-center">
                  <button
                    onClick={() => setExportOpen((v) => !v)}
                    className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors"
                    style={{ color: "var(--color-text-primary)" }}
                    title="Export"
                  >
                    Export ▾
                  </button>
                  {exportOpen && (
                    <div className="absolute right-0 top-full mt-1 z-50 bg-[var(--color-bg-primary)] border border-[var(--color-border)] min-w-[110px]">
                      {(["md", "pdf", "docx"] as const).map((fmt) => (
                        <button
                          key={fmt}
                          onClick={() => handleExport(fmt)}
                          className="block w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-accent)] transition-colors"
                          style={{ color: "var(--color-text-primary)" }}
                        >
                          .{fmt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {!fullscreenMode && (
                  <button
                    onClick={onWritingClose}
                    className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors"
                    style={{ color: "var(--color-text-primary)" }}
                    title="Close writing mode"
                  >
                    ✕ Close
                  </button>
                )}
              </>
            ) : (
              <>
                <ThemeToggle />
                <NavLink href="/dashboard" active={isQueueActive}>
                  Queue
                </NavLink>
                <NavLink href="/lists" active={isListsActive}>
                  Lists
                </NavLink>
                {SHOW_CRATES && (
                  <NavLink href="/crates" active={isCratesActive}>
                    Crates
                  </NavLink>
                )}
                <NavLink href="/settings" active={isSettingsActive}>
                  Settings
                </NavLink>
              </>
            )}
          </div>

          {/* Mobile: Mini Player, Theme Toggle and Menu Button */}
          <div className="flex md:hidden items-center gap-2">
            {/* Mini player — album art + play/pause (mounted guard prevents hydration mismatch) */}
            {mounted && playerCurrent && (
              <button
                onClick={toggle}
                className="compact-touch relative w-6 h-6 flex-shrink-0 overflow-hidden border border-[var(--color-border)] bg-[var(--color-bg-tertiary)]"
                title={isPlaying ? "Pause" : "Play"}
              >
                {mounted && playerCurrent && playerCurrent.cover_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={playerCurrent.cover_url}
                    alt=""
                    className={`w-full h-full object-cover ${isPlaying ? "" : "opacity-50"}`}
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <span className="font-mono text-[8px] text-[var(--color-text-faint)]">
                      {mounted && isPlaying ? "||" : "▶"}
                    </span>
                  </div>
                )}
                {!isPlaying &&
                  mounted &&
                  playerCurrent &&
                  playerCurrent.cover_url && (
                    <div className="absolute inset-0 flex items-center justify-center">
                      <svg
                        width="10"
                        height="10"
                        viewBox="0 0 10 10"
                        fill="var(--color-text-primary)"
                      >
                        <polygon points="3,1 3,9 9,5" />
                      </svg>
                    </div>
                  )}
              </button>
            )}
            <ThemeToggle />
            <button
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
              className="compact-touch text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors p-1"
              aria-label="Toggle mobile menu"
            >
              <svg
                className="h-6 w-6"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 6h16M4 12h16M4 18h16"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Mobile Menu Dropdown */}
      {isMobileMenuOpen && (
        <>
          <div
            className="fixed inset-0 z-10 md:hidden"
            onClick={closeMobileMenu}
            aria-hidden="true"
          />

          <div className="absolute top-full left-0 right-0 bg-[var(--color-bg-primary)] border-b border-[var(--color-border)] z-20 md:hidden">
            <div className="px-5 pt-3 pb-2">
              <SearchBar />
            </div>
            <nav className="border-t border-[var(--color-border-subtle)]">
              {[
                { href: "/dashboard", label: "Queue", active: isQueueActive },
                { href: "/lists", label: "Lists", active: isListsActive },
                ...(SHOW_CRATES
                  ? [
                      {
                        href: "/crates",
                        label: "Crates",
                        active: isCratesActive,
                      },
                    ]
                  : []),
                {
                  href: "/settings",
                  label: "Settings",
                  active: isSettingsActive,
                },
              ].map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={closeMobileMenu}
                  className={`compact-touch flex items-center px-5 py-3 font-mono text-[11px] uppercase tracking-widest no-underline transition-colors border-b border-[var(--color-border-subtle)] ${
                    item.active
                      ? "text-[var(--color-text-primary)] bg-[var(--color-bg-secondary)]"
                      : "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                  }`}
                  style={{
                    color: item.active
                      ? "var(--color-text-primary)"
                      : "var(--color-text-muted)",
                  }}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="px-5 py-3">
              <NowPlaying />
            </div>
          </div>
        </>
      )}
    </nav>
  );
}
