# Build PC Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/build` page where users assemble a PC from marketplace parts using a wireframe visual with clickable component labels and a card-based parts list with a sticky summary panel.

**Architecture:** Client component page (`"use client"`) holds build state (selected parts per slot) in `useState`. Wireframe section with SVG connector lines + skewed chip labels sits above the fold. Below: 2-column component cards grid + sticky summary panel. A modal (`PartPickerModal`) opens on chip/card click, fetches parts via existing `getParts()` API, and fires a callback to update state.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, framer-motion 12, lucide-react

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `frontend/app/build/page.tsx` | Page shell, build state, layout |
| Create | `frontend/components/BuildWireframe.tsx` | Wireframe image + SVG lines + chip labels |
| Create | `frontend/components/BuildCards.tsx` | 2-col component card grid |
| Create | `frontend/components/BuildSummary.tsx` | Sticky summary panel + copy button |
| Create | `frontend/components/PartPickerModal.tsx` | Floating part selector modal |
| Modify | `frontend/components/Navbar.tsx:40` | Remove `disabled` from Build PC link |

---

## Task 1: Enable Build PC nav link

**Files:**
- Modify: `frontend/components/Navbar.tsx:40`

- [ ] **Step 1: Remove `disabled` prop from Build PC link**

In `frontend/components/Navbar.tsx`, change line 40 from:
```tsx
<NavLink href="/build" disabled>Build PC</NavLink>
```
to:
```tsx
<NavLink href="/build">Build PC</NavLink>
```

- [ ] **Step 2: Verify dev server renders it as a real link**

```bash
cd frontend && npm run dev
```
Open http://localhost:3000 — "Build PC" in nav should be a clickable link (no SOON badge), currently 404s (page not created yet). That's expected.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/Navbar.tsx
git commit -m "feat: enable Build PC nav link"
```

---

## Task 2: Define build state types and page shell

**Files:**
- Create: `frontend/app/build/page.tsx`

- [ ] **Step 1: Create the page with build state**

Create `frontend/app/build/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import Navbar from "@/components/Navbar";
import BuildWireframe from "@/components/BuildWireframe";
import BuildCards from "@/components/BuildCards";
import BuildSummary from "@/components/BuildSummary";
import PartPickerModal from "@/components/PartPickerModal";
import type { Part } from "@/lib/api";

export type SlotKey =
  | "cpu" | "gpu" | "ram" | "motherboard"
  | "psu" | "case" | "ssd" | "cooling";

export type BuildState = Record<SlotKey, Part | null>;

const EMPTY_BUILD: BuildState = {
  cpu: null, gpu: null, ram: null, motherboard: null,
  psu: null, case: null, ssd: null, cooling: null,
};

export const SLOT_LABELS: Record<SlotKey, string> = {
  cpu: "CPU", gpu: "GPU", ram: "RAM", motherboard: "Mobo",
  psu: "PSU", case: "Case", ssd: "SSD", cooling: "Cooling",
};

export const SLOT_CATEGORY: Record<SlotKey, string> = {
  cpu: "cpu", gpu: "gpu", ram: "ram", motherboard: "motherboard",
  psu: "psu", case: "case", ssd: "ssd", cooling: "cooling",
};

export default function BuildPage() {
  const [build, setBuild] = useState<BuildState>(EMPTY_BUILD);
  const [activeSlot, setActiveSlot] = useState<SlotKey | null>(null);

  function selectPart(part: Part) {
    if (!activeSlot) return;
    setBuild((prev) => ({ ...prev, [activeSlot]: part }));
    setActiveSlot(null);
  }

  return (
    <div className="flex flex-col min-h-screen" style={{ background: "var(--bg)" }}>
      <Navbar />
      <main className="flex flex-col flex-1">
        <BuildWireframe build={build} onSlotClick={setActiveSlot} />
        <section className="max-w-[1200px] mx-auto w-full px-12 py-16">
          <p className="section-label mb-1">Your Build</p>
          <h2
            className="font-black mb-2"
            style={{ fontSize: "clamp(1.2rem, 2.5vw, 1.5rem)", color: "var(--text)" }}
          >
            Component List
          </h2>
          <p className="text-sm mb-9" style={{ color: "var(--text-muted)" }}>
            Click any slot to browse and select parts from the marketplace.
          </p>
          <div className="flex gap-7 items-start">
            <BuildCards build={build} onSlotClick={setActiveSlot} />
            <BuildSummary build={build} />
          </div>
        </section>
      </main>

      {activeSlot && (
        <PartPickerModal
          slot={activeSlot}
          currentPart={build[activeSlot]}
          onSelect={selectPart}
          onClose={() => setActiveSlot(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify page loads (will error — missing components)**

```bash
cd frontend && npm run dev
```
Navigate to http://localhost:3000/build — expect module not found errors for missing components. That's expected. TypeScript errors are also fine at this stage.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/build/page.tsx
git commit -m "feat: add build page shell with state management"
```

---

## Task 3: BuildWireframe component

**Files:**
- Create: `frontend/components/BuildWireframe.tsx`

The wireframe image lives at `/gaming-pc-wireframe-drawing-line-600nw-2588972631.webp` in the project root. Copy it to `frontend/public/wireframe.webp` so Next.js can serve it.

- [ ] **Step 1: Copy wireframe image to public folder**

```bash
cp /home/azaan/Documents/PPC/gaming-pc-wireframe-drawing-line-600nw-2588972631.webp \
   /home/azaan/Documents/PPC/frontend/public/wireframe.webp
```

- [ ] **Step 2: Create BuildWireframe component**

Create `frontend/components/BuildWireframe.tsx`:

```tsx
"use client";

import Image from "next/image";
import { motion } from "framer-motion";
import type { BuildState, SlotKey } from "@/app/build/page";
import { SLOT_LABELS } from "@/app/build/page";

interface Props {
  build: BuildState;
  onSlotClick: (slot: SlotKey) => void;
}

// Left chips: [slot, chipX (right edge), chipY (center), lineToX, lineToY]
// Right chips: [slot, chipX (left edge), chipY (center), lineToX, lineToY]
// Coordinates are in the 860×480 SVG viewBox space.
// Image center is at (430, 220), image is ~260×300px rendered.
const LEFT_CHIPS: [SlotKey, number, number, number, number][] = [
  ["cpu",         148, 68,  300, 120],
  ["gpu",         148, 138, 300, 180],
  ["ram",         148, 208, 310, 230],
  ["motherboard", 148, 278, 305, 270],
];

const RIGHT_CHIPS: [SlotKey, number, number, number, number][] = [
  ["psu",     712, 68,  560, 150],
  ["case",    712, 138, 555, 200],
  ["ssd",     712, 208, 560, 260],
  ["cooling", 712, 278, 555, 130],
];

function Chip({
  slot,
  build,
  onClick,
  style,
}: {
  slot: SlotKey;
  build: BuildState;
  onClick: () => void;
  style: React.CSSProperties;
}) {
  const selected = build[slot] !== null;
  return (
    <motion.button
      onClick={onClick}
      animate={selected ? {} : { opacity: [1, 0.6, 1] }}
      transition={selected ? {} : { repeat: Infinity, duration: 2.4, ease: "easeInOut" }}
      style={{
        position: "absolute",
        padding: "6px 16px",
        background: selected ? "#7c3aed" : "var(--bg)",
        border: selected ? "2px solid #111112" : "2px solid #111112",
        boxShadow: selected ? "3px 3px 0 #111112" : "3px 3px 0 #111112",
        transform: "skewX(-12deg)",
        fontSize: "11px",
        fontWeight: 800,
        letterSpacing: "1px",
        textTransform: "uppercase",
        color: selected ? "white" : "var(--text)",
        cursor: "pointer",
        whiteSpace: "nowrap",
        fontFamily: "var(--mono)",
        ...style,
      }}
      whileHover={{ background: selected ? "#6d28d9" : "#ede9fe" }}
    >
      {SLOT_LABELS[slot]}{selected ? " ✓" : ""}
    </motion.button>
  );
}

export default function BuildWireframe({ build, onSlotClick }: Props) {
  function lineColor(slot: SlotKey, empty = false) {
    if (empty) return "#d4d4d8";
    return build[slot] ? "#7c3aed" : "#111112";
  }
  function lineDash(slot: SlotKey) {
    return build[slot] ? "none" : "4 3";
  }

  return (
    <section
      style={{
        height: "calc(100vh - 52px)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "40px 24px 32px",
        background: "var(--bg)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <motion.p
        className="section-label"
        style={{ marginBottom: "6px" }}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        Configure your build
      </motion.p>
      <motion.h1
        className="font-black text-center"
        style={{ fontSize: "clamp(1.6rem, 4vw, 2.2rem)", color: "var(--text)", marginBottom: "40px" }}
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        Build a PC
      </motion.h1>

      {/* Wireframe + chips container */}
      <div style={{ position: "relative", width: "860px", height: "480px", maxWidth: "100%" }}>

        {/* SVG connector lines */}
        <svg
          viewBox="0 0 860 480"
          style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", pointerEvents: "none" }}
        >
          {LEFT_CHIPS.map(([slot, cx, cy, lx, ly]) => (
            <line
              key={slot}
              x1={cx} y1={cy} x2={lx} y2={ly}
              stroke={lineColor(slot)}
              strokeWidth="1.5"
              strokeDasharray={lineDash(slot)}
            />
          ))}
          {RIGHT_CHIPS.map(([slot, cx, cy, lx, ly]) => (
            <line
              key={slot}
              x1={cx} y1={cy} x2={lx} y2={ly}
              stroke={lineColor(slot)}
              strokeWidth="1.5"
              strokeDasharray={lineDash(slot)}
            />
          ))}
        </svg>

        {/* Left chips */}
        {LEFT_CHIPS.map(([slot, , cy]) => (
          <Chip
            key={slot}
            slot={slot}
            build={build}
            onClick={() => onSlotClick(slot)}
            style={{ left: 0, top: cy - 16 }}
          />
        ))}

        {/* Wireframe image */}
        <div
          style={{
            position: "absolute", left: "50%", top: "50%",
            transform: "translate(-50%, -50%)",
            border: "2px solid #111112",
            boxShadow: "8px 8px 0 #111112",
          }}
        >
          <Image
            src="/wireframe.webp"
            alt="PC wireframe"
            width={300}
            height={340}
            style={{ display: "block" }}
          />
        </div>

        {/* Right chips */}
        {RIGHT_CHIPS.map(([slot, , cy]) => (
          <Chip
            key={slot}
            slot={slot}
            build={build}
            onClick={() => onSlotClick(slot)}
            style={{ right: 0, top: cy - 16 }}
          />
        ))}
      </div>

      {/* Scroll hint */}
      <motion.div
        style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "4px", marginTop: "32px", color: "var(--text-dim)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6 }}
      >
        <motion.span
          style={{ fontSize: "18px" }}
          animate={{ y: [0, 5, 0] }}
          transition={{ repeat: Infinity, duration: 1.5 }}
        >
          ↓
        </motion.span>
        <span className="section-label">configure parts below</span>
      </motion.div>
    </section>
  );
}
```

- [ ] **Step 3: Verify wireframe section renders**

```bash
cd frontend && npm run dev
```
Navigate to http://localhost:3000/build — should see heading, wireframe image centered with black hard shadow, 8 chip labels around it with SVG lines, scroll hint below. Page will error on BuildCards/BuildSummary/PartPickerModal being missing — that's expected.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/BuildWireframe.tsx frontend/public/wireframe.webp
git commit -m "feat: add BuildWireframe with SVG connector lines and chip labels"
```

---

## Task 4: BuildCards component

**Files:**
- Create: `frontend/components/BuildCards.tsx`

- [ ] **Step 1: Create BuildCards**

Create `frontend/components/BuildCards.tsx`:

```tsx
import type { BuildState, SlotKey } from "@/app/build/page";
import { SLOT_LABELS, SLOT_CATEGORY } from "@/app/build/page";

const ALL_SLOTS: SlotKey[] = [
  "cpu", "gpu", "ram", "motherboard",
  "psu", "case", "ssd", "cooling",
];

interface Props {
  build: BuildState;
  onSlotClick: (slot: SlotKey) => void;
}

export default function BuildCards({ build, onSlotClick }: Props) {
  return (
    <div
      style={{
        flex: 1,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: "14px",
      }}
    >
      {ALL_SLOTS.map((slot) => {
        const part = build[slot];
        const selected = part !== null;
        return (
          <button
            key={slot}
            onClick={() => onSlotClick(slot)}
            style={{
              border: selected
                ? "2px solid #7c3aed"
                : "2px dashed #d4d4d8",
              boxShadow: selected
                ? "3px 3px 0 #7c3aed"
                : "3px 3px 0 #d4d4d8",
              background: selected ? "var(--bg-card)" : "#fafafa",
              padding: "18px 20px",
              textAlign: "left",
              cursor: "pointer",
              transition: "background 0.12s",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#ede9fe";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                selected ? "var(--bg-card)" : "#fafafa";
            }}
          >
            <p
              className="mono"
              style={{
                fontSize: "9px",
                fontWeight: 800,
                letterSpacing: "1.5px",
                textTransform: "uppercase",
                color: selected ? "#7c3aed" : "var(--text-dim)",
                marginBottom: "7px",
              }}
            >
              {SLOT_LABELS[slot]}
            </p>
            {selected ? (
              <>
                <p
                  className="font-semibold"
                  style={{ fontSize: "13px", color: "var(--text)", marginBottom: "4px", lineHeight: 1.3 }}
                >
                  {part!.name}
                </p>
                <p style={{ fontSize: "12px", color: "var(--text-2)", fontWeight: 600 }}>
                  {part!.price_pkr != null
                    ? "Rs\u00a0" + part!.price_pkr.toLocaleString("en-PK")
                    : "Out of stock"}
                </p>
                <p style={{ fontSize: "9px", color: "var(--text-dim)", marginTop: "3px" }}>
                  {part!.source}
                </p>
              </>
            ) : (
              <p style={{ fontSize: "13px", color: "var(--text-dim)", fontStyle: "italic" }}>
                + Select {SLOT_LABELS[slot]}
              </p>
            )}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/BuildCards.tsx
git commit -m "feat: add BuildCards 2-col component grid"
```

---

## Task 5: BuildSummary component

**Files:**
- Create: `frontend/components/BuildSummary.tsx`

- [ ] **Step 1: Create BuildSummary**

Create `frontend/components/BuildSummary.tsx`:

```tsx
"use client";

import type { BuildState, SlotKey } from "@/app/build/page";
import { SLOT_LABELS } from "@/app/build/page";

const ALL_SLOTS: SlotKey[] = [
  "cpu", "gpu", "ram", "motherboard",
  "psu", "case", "ssd", "cooling",
];

interface Props {
  build: BuildState;
}

export default function BuildSummary({ build }: Props) {
  const total = ALL_SLOTS.reduce((sum, slot) => {
    return sum + (build[slot]?.price_pkr ?? 0);
  }, 0);

  function copyBuild() {
    const lines = ALL_SLOTS.map((slot) => {
      const part = build[slot];
      const price = part?.price_pkr != null
        ? "Rs " + part.price_pkr.toLocaleString("en-PK")
        : "—";
      return `${SLOT_LABELS[slot]}: ${part?.name ?? "Not selected"} (${price})`;
    });
    lines.push(`\nTotal: Rs ${total.toLocaleString("en-PK")}`);
    navigator.clipboard.writeText(lines.join("\n"));
  }

  return (
    <div
      style={{
        width: "240px",
        flexShrink: 0,
        border: "2px solid #111112",
        background: "var(--bg-card)",
        boxShadow: "5px 5px 0 #111112",
        position: "sticky",
        top: "72px",
      }}
    >
      {/* Header */}
      <div
        style={{
          background: "#111112",
          color: "white",
          padding: "12px 16px",
          fontSize: "10px",
          fontWeight: 800,
          letterSpacing: "2px",
          textTransform: "uppercase",
          fontFamily: "var(--mono)",
        }}
      >
        Build Summary
      </div>

      {/* Rows */}
      <div style={{ padding: "16px" }}>
        {ALL_SLOTS.map((slot) => {
          const part = build[slot];
          return (
            <div
              key={slot}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                padding: "7px 0",
                borderBottom: "1px solid var(--border)",
                gap: "8px",
              }}
            >
              <span
                className="mono"
                style={{ fontSize: "9px", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", flexShrink: 0 }}
              >
                {SLOT_LABELS[slot]}
              </span>
              <span
                className="mono"
                style={{
                  fontSize: "10px",
                  fontWeight: 600,
                  color: part?.price_pkr != null ? "var(--text-2)" : "var(--text-dim)",
                  textAlign: "right",
                }}
              >
                {part?.price_pkr != null
                  ? "Rs\u00a0" + part.price_pkr.toLocaleString("en-PK")
                  : "—"}
              </span>
            </div>
          );
        })}

        {/* Total */}
        <div
          style={{
            marginTop: "14px",
            paddingTop: "14px",
            borderTop: "2px solid #111112",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span
            className="mono"
            style={{ fontSize: "10px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "1px" }}
          >
            Total
          </span>
          <span
            className="mono"
            style={{ fontSize: "18px", fontWeight: 900, color: "#7c3aed" }}
          >
            Rs&nbsp;{total.toLocaleString("en-PK")}
          </span>
        </div>

        {/* Copy button */}
        <button
          onClick={copyBuild}
          style={{
            width: "100%",
            marginTop: "16px",
            padding: "10px",
            background: "#7c3aed",
            color: "white",
            border: "2px solid #111112",
            boxShadow: "3px 3px 0 #111112",
            fontSize: "10px",
            fontWeight: 800,
            letterSpacing: "1px",
            textTransform: "uppercase",
            cursor: "pointer",
            transform: "skewX(-6deg)",
            fontFamily: "var(--mono)",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "#6d28d9"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "#7c3aed"; }}
        >
          Copy Build List
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/BuildSummary.tsx
git commit -m "feat: add BuildSummary sticky panel with total and copy"
```

---

## Task 6: PartPickerModal component

**Files:**
- Create: `frontend/components/PartPickerModal.tsx`

- [ ] **Step 1: Create PartPickerModal**

Create `frontend/components/PartPickerModal.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import Image from "next/image";
import { motion, AnimatePresence } from "framer-motion";
import { getParts, getFilterOptions } from "@/lib/api";
import type { Part, FilterOptions } from "@/lib/api";
import type { SlotKey } from "@/app/build/page";
import { SLOT_LABELS, SLOT_CATEGORY } from "@/app/build/page";

// Spec filter keys relevant per category
const CATEGORY_FILTERS: Record<string, (keyof FilterOptions)[]> = {
  gpu:         ["vram", "brand"],
  cpu:         ["socket", "brand"],
  ram:         ["ddr_type", "speed"],
  motherboard: ["socket", "chipset"],
  psu:         ["wattage", "rating"],
  case:        ["form_factor"],
  ssd:         ["interface", "capacity"],
  cooling:     ["type"],
};

interface Props {
  slot: SlotKey;
  currentPart: Part | null;
  onSelect: (part: Part) => void;
  onClose: () => void;
}

export default function PartPickerModal({ slot, currentPart, onSelect, onClose }: Props) {
  const category = SLOT_CATEGORY[slot];
  const [parts, setParts] = useState<Part[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"price_asc" | "price_desc">("price_asc");
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({});
  const [activeFilters, setActiveFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  // Load filter options once on open
  useEffect(() => {
    getFilterOptions(category).then(setFilterOptions);
  }, [category]);

  // Load parts whenever filters/sort change
  const loadParts = useCallback(async () => {
    setLoading(true);
    const result = await getParts({
      category,
      sort,
      limit: 50,
      ...activeFilters,
    });
    setParts(result.items);
    setTotal(result.total);
    setLoading(false);
  }, [category, sort, activeFilters]);

  useEffect(() => { loadParts(); }, [loadParts]);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function toggleFilter(key: string, value: string) {
    setActiveFilters((prev) => {
      if (prev[key] === value) {
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: value };
    });
  }

  const filteredParts = search.trim()
    ? parts.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()))
    : parts;

  const relevantFilterKeys = CATEGORY_FILTERS[category] ?? [];

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        style={{
          position: "fixed", inset: 0,
          background: "rgba(244,244,245,0.75)",
          backdropFilter: "blur(4px)",
          display: "flex", alignItems: "center", justifyContent: "center",
          zIndex: 200, padding: "24px",
        }}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.97, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.97 }}
          transition={{ duration: 0.15 }}
          onClick={(e) => e.stopPropagation()}
          style={{
            width: "820px", maxWidth: "100%",
            background: "var(--bg-card)",
            border: "2px solid #111112",
            boxShadow: "10px 10px 0 #111112",
            display: "flex", flexDirection: "column",
            maxHeight: "86vh",
          }}
        >
          {/* Header */}
          <div
            style={{
              background: "#111112", color: "white",
              padding: "16px 22px",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}
          >
            <span
              className="mono"
              style={{ fontSize: "12px", fontWeight: 800, letterSpacing: "2px", textTransform: "uppercase" }}
            >
              Select — {SLOT_LABELS[slot]}
            </span>
            <button
              onClick={onClose}
              style={{ background: "none", border: "none", color: "white", fontSize: "18px", fontWeight: 800, cursor: "pointer", lineHeight: 1 }}
            >
              ✕
            </button>
          </div>

          {/* Search */}
          <div style={{ padding: "16px 22px", borderBottom: "1px solid var(--border)" }}>
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={`Search ${SLOT_LABELS[slot]}s...`}
              style={{
                width: "100%", padding: "10px 14px",
                border: "2px solid #111112",
                background: "white", fontSize: "13px", outline: "none",
                boxShadow: "2px 2px 0 #111112",
                fontFamily: "inherit",
              }}
              onFocus={(e) => {
                e.target.style.borderColor = "#7c3aed";
                e.target.style.boxShadow = "2px 2px 0 #7c3aed";
              }}
              onBlur={(e) => {
                e.target.style.borderColor = "#111112";
                e.target.style.boxShadow = "2px 2px 0 #111112";
              }}
            />
          </div>

          {/* Filter chips */}
          {relevantFilterKeys.length > 0 && (
            <div
              style={{
                padding: "10px 22px",
                borderBottom: "2px solid #111112",
                display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap",
              }}
            >
              {relevantFilterKeys.map((key) => {
                const values = filterOptions[key] ?? [];
                if (values.length === 0) return null;
                return (
                  <div key={key} style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                    <span
                      className="mono"
                      style={{ fontSize: "9px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", color: "var(--text-muted)" }}
                    >
                      {key.replace("_", " ")}:
                    </span>
                    {values.map((v) => {
                      const active = activeFilters[key] === v;
                      return (
                        <button
                          key={v}
                          onClick={() => toggleFilter(key, v)}
                          style={{
                            padding: "4px 12px",
                            border: active ? "1.5px solid #7c3aed" : "1.5px solid var(--border)",
                            background: active ? "#f0ebff" : "white",
                            boxShadow: active ? "2px 2px 0 #7c3aed" : "none",
                            transform: "skewX(-8deg)",
                            fontSize: "10px", fontWeight: 700, letterSpacing: "0.5px",
                            color: active ? "#7c3aed" : "var(--text-muted)",
                            cursor: "pointer",
                            fontFamily: "var(--mono)",
                          }}
                        >
                          {v}
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}

          {/* Part list */}
          <div style={{ overflowY: "auto", flex: 1 }}>
            {loading ? (
              <div style={{ padding: "40px", textAlign: "center", color: "var(--text-dim)" }} className="mono">
                Loading…
              </div>
            ) : filteredParts.length === 0 ? (
              <div style={{ padding: "40px", textAlign: "center", color: "var(--text-dim)" }} className="mono">
                No parts found
              </div>
            ) : (
              filteredParts.map((part) => {
                const isCurrent = currentPart?.id === part.id;
                return (
                  <div
                    key={part.id}
                    style={{
                      display: "flex", alignItems: "center", gap: "14px",
                      padding: "14px 22px",
                      borderBottom: "1px solid var(--border)",
                      background: isCurrent ? "#f0ebff" : "transparent",
                      borderLeft: isCurrent ? "3px solid #7c3aed" : "3px solid transparent",
                      cursor: "pointer",
                      transition: "background 0.1s",
                    }}
                    onMouseEnter={(e) => {
                      if (!isCurrent) (e.currentTarget as HTMLDivElement).style.background = "#ede9fe";
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLDivElement).style.background = isCurrent ? "#f0ebff" : "transparent";
                    }}
                  >
                    {/* Thumbnail */}
                    <div
                      style={{
                        width: "52px", height: "52px",
                        background: "var(--bg-section)",
                        border: "1.5px solid var(--border)",
                        flexShrink: 0,
                        display: "flex", alignItems: "center", justifyContent: "center",
                        overflow: "hidden",
                      }}
                    >
                      {part.thumbnail_url ? (
                        <Image src={part.thumbnail_url} alt={part.name} width={52} height={52} style={{ objectFit: "contain" }} unoptimized />
                      ) : (
                        <span style={{ fontSize: "9px", color: "var(--text-dim)" }}>IMG</span>
                      )}
                    </div>

                    {/* Info */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {part.name}
                      </p>
                      <p style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "3px" }}>
                        {Object.values(part.specs ?? {}).filter(Boolean).slice(0, 3).join(" · ")}
                      </p>
                    </div>

                    {/* Price */}
                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <p style={{ fontSize: "14px", fontWeight: 800, color: "var(--text)" }}>
                        {part.price_pkr != null ? "Rs\u00a0" + part.price_pkr.toLocaleString("en-PK") : "—"}
                      </p>
                      <p style={{ fontSize: "9px", color: "var(--text-dim)", marginTop: "2px" }}>{part.source}</p>
                    </div>

                    {/* Select button */}
                    <button
                      onClick={() => onSelect(part)}
                      style={{
                        padding: "8px 18px",
                        background: isCurrent ? "#7c3aed" : "var(--bg)",
                        border: "2px solid #111112",
                        boxShadow: "2px 2px 0 #111112",
                        fontSize: "9px", fontWeight: 800, letterSpacing: "1px",
                        textTransform: "uppercase",
                        color: isCurrent ? "white" : "var(--text)",
                        cursor: "pointer",
                        transform: "skewX(-8deg)",
                        flexShrink: 0,
                        fontFamily: "var(--mono)",
                      }}
                      onMouseEnter={(e) => {
                        const btn = e.currentTarget as HTMLButtonElement;
                        btn.style.background = "#7c3aed";
                        btn.style.color = "white";
                      }}
                      onMouseLeave={(e) => {
                        const btn = e.currentTarget as HTMLButtonElement;
                        btn.style.background = isCurrent ? "#7c3aed" : "var(--bg)";
                        btn.style.color = isCurrent ? "white" : "var(--text)";
                      }}
                    >
                      {isCurrent ? "Selected ✓" : "Select"}
                    </button>
                  </div>
                );
              })
            )}
          </div>

          {/* Footer */}
          <div
            style={{
              padding: "14px 22px",
              borderTop: "2px solid #111112",
              display: "flex", justifyContent: "space-between", alignItems: "center",
              background: "var(--bg)",
            }}
          >
            <span className="mono" style={{ fontSize: "11px", color: "var(--text-muted)", fontWeight: 600 }}>
              {total} {SLOT_LABELS[slot]}s found
            </span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as "price_asc" | "price_desc")}
              style={{ border: "1.5px solid var(--border)", padding: "6px 10px", fontSize: "11px", background: "white", cursor: "pointer" }}
            >
              <option value="price_asc">Price: Low → High</option>
              <option value="price_desc">Price: High → Low</option>
            </select>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
```

- [ ] **Step 2: Verify full page works end-to-end**

```bash
cd frontend && npm run dev
```
Navigate to http://localhost:3000/build. Verify:
- Wireframe section fills viewport, chips visible with SVG lines
- Scrolling down shows 8 component cards + sticky summary panel
- Clicking any chip or card opens modal with correct category pre-filtered
- Selecting a part closes modal, updates card and chip (shows ✓), updates summary total
- Copy Build List button copies formatted text to clipboard
- Escape key closes modal
- Clicking backdrop closes modal

- [ ] **Step 3: Fix any TypeScript errors**

```bash
cd frontend && npx tsc --noEmit
```
Fix any type errors reported.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/PartPickerModal.tsx
git commit -m "feat: add PartPickerModal with search, spec filters, and part selection"
```

---

## Task 7: Final polish and TypeScript check

**Files:**
- Modify: `frontend/app/build/page.tsx` (if any issues found)

- [ ] **Step 1: Run TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors. Fix any remaining type issues.

- [ ] **Step 2: Check Next.js build**

```bash
cd frontend && npm run build
```
Expected: build succeeds with no errors. Fix any issues.

- [ ] **Step 3: Final manual test**

With `npm run dev` running, verify:
1. `/` landing page — Build PC nav link is no longer disabled, no SOON badge
2. `/build` — wireframe section fills viewport
3. All 8 chips are visible, dashed lines for empty slots
4. Clicking CPU chip → modal opens, shows CPUs
5. Select a CPU → modal closes, CPU chip turns purple + ✓, card fills in, summary updates
6. Summary total reflects selected part price
7. Copy Build List → check clipboard has formatted text
8. Clicking different chip reuses modal for correct category

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete Build PC page"
```
