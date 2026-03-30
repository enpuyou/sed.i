"use client";

import { useEffect, useRef, useState } from "react";
import DemoBlock from "@/components/DemoBlock";

interface Feature {
  num: string;
  title: string;
  description: string;
  detail?: string;
  clipSrc?: string;
  placeholderContent?: React.ReactNode;
}

interface FeatureShowcaseProps {
  features: Feature[];
}

export default function FeatureShowcase({ features }: FeatureShowcaseProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const demoRefs = useRef<(HTMLDivElement | null)[]>([]);

  // IntersectionObserver watches which demo is in view and swaps the text
  useEffect(() => {
    const observers: IntersectionObserver[] = [];

    demoRefs.current.forEach((el: HTMLDivElement | null, i: number) => {
      if (!el) return;
      const obs = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) setActiveIndex(i);
        },
        // Trigger when demo centre crosses the middle of the viewport
        { rootMargin: "-40% 0px -40% 0px", threshold: 0 },
      );
      obs.observe(el);
      observers.push(obs);
    });

    return () => observers.forEach((o) => o.disconnect());
  }, []);

  const active = features[activeIndex];

  return (
    <div className="relative max-w-5xl mx-auto px-6 lg:px-16">
      <div className="flex gap-8 lg:gap-14">

        {/* ── Left: sticky text panel ── */}
        <div className="hidden md:flex w-64 lg:w-72 flex-shrink-0">
          <div className="sticky top-0 h-screen flex items-center">
            <div>
              {/* Num — cross-fades on change */}
              <span
                key={`num-${activeIndex}`}
                className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] block animate-text-swap"
              >
                {active.num}
              </span>

              {/* Title */}
              <h2
                key={`title-${activeIndex}`}
                className="mt-3 font-serif text-4xl sm:text-5xl font-normal text-[var(--color-text-primary)] animate-text-swap"
                style={{ letterSpacing: "-0.02em" }}
              >
                {active.title}
              </h2>

              {/* Description */}
              <p
                key={`desc-${activeIndex}`}
                className="mt-4 text-base text-[var(--color-text-secondary)] leading-relaxed max-w-xs animate-text-swap"
                style={{ fontFamily: "'Helvetica Neue', Helvetica, Arial, sans-serif", fontWeight: "var(--feature-desc-weight)", letterSpacing: "-0.01em" } as React.CSSProperties}
              >
                {active.description}
              </p>

              {/* Detail tags */}
              {active.detail && (
                <p
                  key={`detail-${activeIndex}`}
                  className="mt-4 font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] animate-text-swap"
                >
                  {active.detail}
                </p>
              )}

              {/* Progress dots */}
              <div className="mt-8 flex gap-2">
                {features.map((_, i) => (
                  <div
                    key={i}
                    className="h-px transition-all duration-500"
                    style={{
                      width: i === activeIndex ? "24px" : "8px",
                      background:
                        i === activeIndex
                          ? "var(--color-text-primary)"
                          : "var(--color-border)",
                    }}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── Right: scrolling demo cards ── */}
        <div className="flex-1 py-[30vh]">
          {features.map((f, i) => (
            <div
              key={f.num}
              ref={(el) => { demoRefs.current[i] = el; }}
              className="mb-[20vh] last:mb-0 demo-card-reveal"
            >
              {/* Mobile-only text (shown above each demo on small screens) */}
              <div className="md:hidden mb-6">
                <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)]">
                  {f.num}
                </span>
                <h2
                  className="mt-2 font-serif text-3xl font-normal text-[var(--color-text-primary)]"
                  style={{ letterSpacing: "-0.02em" }}
                >
                  {f.title}
                </h2>
                <p className="mt-3 text-sm text-[var(--color-text-secondary)] leading-relaxed">
                  {f.description}
                </p>
              </div>

              <DemoBlock
                clipSrc={f.clipSrc}
                placeholderContent={f.placeholderContent}
              />
            </div>
          ))}
        </div>

      </div>
    </div>
  );
}
