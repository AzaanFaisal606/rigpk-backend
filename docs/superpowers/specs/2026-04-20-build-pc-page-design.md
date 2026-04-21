# Build a PC Page — Design Spec

**Date:** 2026-04-20  
**Status:** Approved

---

## Overview

New page at `/build` allowing users to assemble a PC from scraped marketplace parts. Two sections: a full-viewport wireframe visual with clickable component labels, and a scrollable parts configuration section below.

---

## Page Structure

### Section 1 — Wireframe Hero (100vh)

Full viewport height. Light background (`#f4f4f5`) matching rest of site.

- Small purple section label: `CONFIGURE YOUR BUILD`
- Large bold heading: `Build a PC`
- Central wireframe image (`gaming-pc-wireframe-drawing-line-600nw-2588972631.webp`, 600×600) — rendered ~340×380px, with `border: 2px solid #111112` and `box-shadow: 8px 8px 0 #111112`
- 4 component chips on the left, 4 on the right, positioned with absolute layout around the image
- SVG overlay layer draws thin connector lines (solid purple for selected, dashed black for unselected, dashed gray for empty) from each chip to its hardcoded hotspot coordinate on the image
- Scroll hint arrow at the bottom (`↓ configure parts below`) with bounce animation

**Component chip style:**
- `transform: skewX(-12deg)` parallelogram shape
- `border: 2px solid #111112`, `box-shadow: 3px 3px 0 #111112`
- States:
  - **Empty:** gray border/shadow, muted text
  - **Unselected (hover ready):** black border, black text
  - **Selected:** `background: #7c3aed`, white text, black border/shadow
- Hover: `background: #ede9fe`
- Clicking any chip opens the Part Picker Modal

**Component layout (left/right of image):**

| Left | Right |
|------|-------|
| CPU | PSU |
| GPU | Case |
| RAM | SSD |
| Motherboard | Cooling |

**SVG connector lines:**
- Selected: solid purple `#7c3aed`, 1.5px stroke
- Unselected: dashed black `#111112`, 1.5px, `stroke-dasharray: 4 3`
- Empty: dashed gray `#d4d4d8`, 1.5px
- Hotspot coordinates hardcoded per component against the 600×600 image coordinate space, scaled to rendered size

---

### Section 2 — Build Configuration

Below the hero. `max-width: 1200px`, centered, `padding: 64px 48px`.

**Layout:** `display: flex; gap: 28px`  
Left: 2-column cards grid (flex: 1)  
Right: sticky summary panel (240px wide, `top: 72px`)

#### Component Cards Grid

8 cards in a `grid-template-columns: 1fr 1fr` grid.

Each card:
- `border: 2px solid #111112`, `box-shadow: 3px 3px 0 #111112`, `padding: 18px 20px`
- **Empty state:** `border-style: dashed`, gray border/shadow, dashed gray outline, italic placeholder text `+ Select a {slot}`
- **Selected state:** purple border/shadow (`#7c3aed`)
- Hover: `background: #ede9fe`
- Clicking opens Part Picker Modal filtered to that component's category
- Displays: slot label (purple, 9px uppercase), part name (13px bold), price (12px), source retailer (9px muted)

#### Sticky Summary Panel

- `border: 2px solid #111112`, `box-shadow: 5px 5px 0 #111112`
- Black header bar: `BUILD SUMMARY` (white, uppercase)
- Rows: slot name | price (or `—` if empty)
- Divider line then **Total** row: slot label + large purple total price (`font-size: 18px, font-weight: 900, color: #7c3aed`)
- `Copy Build List` button: skewed (`skewX(-6deg)`), purple fill, black border + hard shadow

---

## Part Picker Modal

Opens on click of any chip or card slot. Closes on `✕` or clicking backdrop.

**Dimensions:** 820px wide, max 86vh tall  
**Style:** `border: 2px solid #111112`, `box-shadow: 10px 10px 0 #111112`  
**Backdrop:** `rgba(244,244,245,0.7)` + `backdrop-filter: blur(4px)`

**Structure (top → bottom):**
1. **Header bar** — black bg, `Select — {Category}` title + `✕` close button
2. **Search input** — full-width, `border: 2px solid #111112`, hard shadow, purple focus state
3. **Filter row** — spec filter chips (skewed `skewX(-8deg)`): relevant spec keys for the category (e.g. VRAM for GPU, DDR type for RAM) + Source filter. Active chip: purple border + bg + shadow
4. **Scrollable part list** — rows with thumbnail (52×52), name + meta, price + source, Select button
5. **Footer** — result count (left) + sort dropdown (right)

**Part row:**
- `padding: 14px 22px`, hover `#ede9fe`
- Currently-selected part: `background: #f0ebff`, purple left border (3px)
- Select button: skewed parallelogram, hover turns purple fill; selected state stays purple with `Selected ✓`

**Data source:** `getParts({ category, ...specFilters, sort, limit: 50 })` — reuses existing API. Spec filter chips populated via `getFilterOptions(category)`.

**State:** modal open/close + selected parts stored in React `useState` on the build page (client component). Build state is ephemeral (no persistence, no URL sync needed for v1).

---

## Animations

All via `framer-motion` (already installed):
- Page load: heading + subtitle fade-in with stagger (matches Hero pattern)
- Chips: subtle pulse/glow on empty unselected chips to invite interaction
- Card hover: standard CSS transition (`background 0.12s`)
- Modal: fade + slight scale-up on open (`initial: opacity 0, scale 0.97`)
- Scroll hint arrow: CSS `bounce` keyframe (already defined pattern)
- SVG connector lines: `stroke-dashoffset` dash-flow animation on unselected lines (matches DiagLines pattern)

---

## Routing & Navigation

- Page: `app/build/page.tsx` — **client component** (`"use client"`) since build state is interactive
- Navbar: remove `disabled` prop from `Build PC` nav link, remove `SOON` badge

---

## Components to Create

| File | Purpose |
|------|---------|
| `app/build/page.tsx` | Main build page (client component) |
| `components/BuildWireframe.tsx` | Wireframe image + SVG overlay + chips |
| `components/BuildCards.tsx` | 2-col component cards grid |
| `components/BuildSummary.tsx` | Sticky summary panel |
| `components/PartPickerModal.tsx` | Floating part selector modal |

---

## Reused Existing Code

- `lib/api.ts` — `getParts()`, `getFilterOptions()` — no changes needed
- `components/Navbar.tsx` — remove disabled state from Build PC link
- CSS variables — `--purple`, `--bg`, `--border`, `--text`, etc.
- `framer-motion` — already installed
- `lucide-react` — for category icons in modal meta

---

## Out of Scope (v1)

- Compatibility warnings / socket matching
- Build persistence (localStorage, URL, or DB)
- Share build link
- Export to PDF
- Price history on modal parts
