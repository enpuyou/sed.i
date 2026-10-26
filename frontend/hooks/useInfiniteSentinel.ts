"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  onVisible: () => void;
  enabled: boolean;
  // Scroll container to observe against; null = the viewport.
  root?: Element | null;
  // Re-arm the observer when this changes, so a sentinel that is still on
  // screen after new rows were appended fires again.
  rearmKey?: unknown;
}

// Calls onVisible when the returned ref's element scrolls into view.
export function useInfiniteSentinel({
  onVisible,
  enabled,
  root = null,
  rearmKey,
}: Options) {
  const [node, setNode] = useState<Element | null>(null);
  const onVisibleRef = useRef(onVisible);

  useEffect(() => {
    onVisibleRef.current = onVisible;
  }, [onVisible]);

  const supported = typeof IntersectionObserver !== "undefined";

  useEffect(() => {
    if (!enabled || !node || !supported) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          onVisibleRef.current();
        }
      },
      { root, rootMargin: "400px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [enabled, node, root, supported, rearmKey]);

  const ref = useCallback((el: Element | null) => setNode(el), []);
  return { ref, supported };
}
