"use client";

import { useEffect, useRef, useState } from "react";

interface DemoBlockProps {
  clipSrc?: string; // base path without extension e.g. "/clips/01-save"
  placeholderLabel?: string;
  placeholderContent?: React.ReactNode;
  aspectRatio?: "4/3" | "16/9" | "3/2" | "16/10";
  size?: "full" | "medium";
}

const ASPECT_PADDING: Record<string, string> = {
  "4/3": "75%",
  "16/9": "56.25%",
  "3/2": "66.67%",
  "16/10": "62.5%",
};

export default function DemoBlock({
  clipSrc,
  placeholderLabel = "screen recording · coming soon",
  placeholderContent,
  aspectRatio = "16/10",
  size = "full",
}: DemoBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [visible, setVisible] = useState(false);

  // Fade in + autoplay on scroll into view
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          videoRef.current?.play().catch(() => {});
        } else {
          videoRef.current?.pause();
        }
      },
      { threshold: 0.2 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={containerRef}
      className={`w-full transition-all duration-700 ease-out ${
        visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"
      } ${size === "medium" ? "max-w-sm mx-auto" : ""}`}
    >
      {/* Aspect-ratio wrapper */}
      <div
        className="relative w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)]"
        style={{ paddingBottom: ASPECT_PADDING[aspectRatio] }}
      >
        <div className="absolute inset-0 flex flex-col items-center justify-center overflow-hidden">
          {clipSrc ? (
            <video
              ref={videoRef}
              autoPlay
              loop
              muted
              playsInline
              className="w-full h-full object-cover"
            >
              <source src={`${clipSrc}.webm`} type="video/webm" />
              <source src={`${clipSrc}.mp4`} type="video/mp4" />
            </video>
          ) : (
            <>
              {placeholderContent ?? <DefaultPlaceholder />}
              <span className="absolute bottom-3 font-mono text-[9px] uppercase tracking-widest text-[var(--color-text-faint)]">
                {placeholderLabel}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function DefaultPlaceholder() {
  return (
    <div className="w-3/4 flex flex-col gap-2">
      {[90, 65, 80, 55, 75].map((w, i) => (
        <div
          key={i}
          className="h-2 bg-[var(--color-border)] rounded-sm"
          style={{ width: `${w}%` }}
        />
      ))}
    </div>
  );
}
