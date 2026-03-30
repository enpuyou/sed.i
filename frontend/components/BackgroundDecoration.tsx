"use client";

import { useEffect, useState } from "react";

const IMAGES = [
  "G37AjtAXcAAEWHs.jpeg",
  "G3ApVFdXkAAPj-m.jpeg",
  "G3QEP0vXcAAar5X.jpeg",
  "G4x5-nhacAA9pxO.jpeg",
  "G6BUR4QW4AEacLM.jpeg",
  "G8C5KJWXsAIcHv_.jpeg",
  "GOH8_HBXIAAjaYA.jpeg",
  "GkznhbBXUAExycV.jpeg",
  "Gp3xXltX0AEFi_O.jpeg",
  "GrQ_uQTWAAAOBDl.jpeg",
  "GxxblNmWoAAgYPn.jpeg",
  "R-6576-002.png",
];

interface ImageState {
  src: string;
  x: number; // percentage (0-100)
  y: number; // percentage (0-100)
  rotation: number; // degrees
  scale: number;
  zIndex: number;
  hoverX: number;
  hoverY: number;
}

interface Thread {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  cp1x: number;
  cp1y: number;
  cp2x: number;
  cp2y: number;
}

export default function BackgroundDecoration() {
  const [images, setImages] = useState<ImageState[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // 1. Select random subset. Fewer images because they are larger.
    const count = Math.floor(Math.random() * 2) + 5; // 5 to 6 images
    const shuffled = [...IMAGES].sort(() => 0.5 - Math.random());
    const selected = shuffled.slice(0, count);

    // 2. Generate random positions with distance check
    const placed: { x: number; y: number }[] = [];

    const newImages: ImageState[] = selected.map((filename, i) => {
      let x = 0,
        y = 0;
      let valid = false;
      let attempts = 0;

      while (!valid && attempts < 50) {
        // X: 10% to 90%
        x = Math.random() * 80 + 10;
        // Y: 15% to 85%
        y = Math.random() * 70 + 15;

        // Avoid hero center and the left text column rectangle.
        // Text column sits at roughly x: 15-42%, y: 20-80% of viewport.
        const inHeroCenter = x > 35 && x < 65 && y > 35 && y < 65;
        const inTextBlock = x > 15 && x < 42 && y > 20 && y < 80;
        const inCenter = inHeroCenter || inTextBlock;

        // 2. Avoid overlap with existing
        // roughly 20% distance threshold
        const tooClose = placed.some((p) => {
          const dx = p.x - x;
          const dy = p.y - y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          return dist < 18; // Minimum distance %
        });

        if (!inCenter && !tooClose) {
          valid = true;
        }
        attempts++;
      }

      // If we failed to find a spot, force it to corners
      if (!valid) {
        const corners = [
          { x: 15, y: 15 },
          { x: 85, y: 15 },
          { x: 15, y: 85 },
          { x: 85, y: 85 },
        ];
        const corner = corners[i % 4];
        x = corner.x + (Math.random() * 10 - 5);
        y = corner.y + (Math.random() * 10 - 5);
      }

      placed.push({ x, y });

      return {
        src: `/img/${filename}`,
        x,
        y,
        rotation: Math.random() * 30 - 15, // -15 to 15 deg
        scale: Math.random() * 0.3 + 0.8, // 0.8 to 1.1 scale
        zIndex: i,
        hoverX: Math.random() * 40 - 20, // -20px to 20px
        hoverY: Math.random() * 40 - 20, // -20px to 20px
      };
    });

    setImages(newImages);

    // 3. Generate connection threads
    // Connect image i to i+1 for a few pairs
    const newThreads: Thread[] = [];
    // Only connect 60% of them to form chains or loose pairs
    for (let i = 0; i < newImages.length - 1; i++) {
      if (Math.random() > 0.4) {
        const start = newImages[i];
        const end = newImages[i + 1];

        // Random Control Points for squiggly Bezier
        // Offset from midpoint
        const midX = (start.x + end.x) / 2;
        const midY = (start.y + end.y) / 2;

        // Control points deviate from the straight line
        newThreads.push({
          id: `thread-${i}`,
          x1: start.x + 5, // approx center of image (assuming ~10% width)
          y1: start.y + 10, // approx center (assuming ~20% height aspect)
          x2: end.x + 5,
          y2: end.y + 10,
          cp1x: midX + (Math.random() * 20 - 10),
          cp1y: midY + (Math.random() * 20 - 10),
          cp2x: midX + (Math.random() * 20 - 10),
          cp2y: midY + (Math.random() * 20 - 10),
        });
      }
    }

    setThreads(newThreads);
    setMounted(true);
  }, []);

  if (!mounted) return null;

  return (
    <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden select-none opacity-10 dark:opacity-90">
      {/* SVG Container for Lines */}
      <svg className="absolute inset-0 w-full h-full">
        {threads.map((t) => (
          <path
            key={t.id}
            d={`M ${t.x1}% ${t.y1}% C ${t.cp1x}% ${t.cp1y}%, ${t.cp2x}% ${t.cp2y}%, ${t.x2}% ${t.y2}%`}
            fill="none"
            stroke="var(--color-text-faint)"
            strokeWidth="1.5"
            strokeDasharray="4 4" // optional: dashed creates a threaded look
            className="animate-draw-in opacity-50"
            style={{ strokeLinecap: "round" }}
          />
        ))}
      </svg>

      {/* Images */}
      {images.map((img, _i) => (
        <div
          key={img.src}
          className="absolute transition-opacity duration-1000 ease-out"
          style={{
            top: `${img.y}%`,
            left: `${img.x}%`,
            transform: `translate(-50%, -50%) rotate(${img.rotation}deg) scale(${img.scale})`,
            zIndex: img.zIndex,
            width: "250px", // larger base width
            maxWidth: "35vw", // responsiveness
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={img.src}
            alt=""
            className={`w-full h-auto ${img.src.includes("R-6576-002.png") ? "drop-shadow-xl" : "shadow-xl"} brightness-95 opacity-90 hover:brightness-110 hover:translate-x-[var(--hover-x)] hover:translate-y-[var(--hover-y)] transition-all duration-3000 pointer-events-auto`}
            style={
              {
                "--hover-x": `${img.hoverX}px`,
                "--hover-y": `${img.hoverY}px`,
              } as React.CSSProperties
            }
          />
        </div>
      ))}
    </div>
  );
}
