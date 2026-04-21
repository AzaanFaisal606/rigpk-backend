# Market Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `/market` browse page — list view of all scraped parts with filters (category, source, price range) and sort (price asc/desc), backed by a new `GET /api/parts` FastAPI endpoint.

**Architecture:** URL search params drive all state (`/market?category=gpu&sort=price_asc`). The page is a Next.js async server component that fetches filtered data server-side. A client `<FilterBar>` component reads the URL and pushes new params via `useRouter`. Part rows are rendered as a server-rendered list with thumbnail, name (links to retailer), price, source badge, and category tag.

**Tech Stack:** FastAPI (Python), SQLite via existing `db.database.Database`, Next.js 16 App Router, Tailwind CSS v4, framer-motion, lucide-react

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/routers/parts.py` | Add `GET /api/parts` endpoint |
| Modify | `db/database.py` | Add `list_parts(...)` query method |
| Modify | `frontend/lib/api.ts` | Add `getParts()` and `Part` type |
| Create | `frontend/app/market/page.tsx` | Server component — fetches + renders market page |
| Create | `frontend/components/FilterBar.tsx` | Client component — category/source/price/sort controls |
| Create | `frontend/components/PartRow.tsx` | Single list-item row (thumbnail, name, price, badges) |
| Modify | `frontend/components/Navbar.tsx` | Enable Market nav link (remove `disabled`) |

---

## Task 1: Add `list_parts` to the database layer

**Files:**
- Modify: `db/database.py` (after line 136, before `get_price_history`)

- [ ] **Step 1: Add `list_parts` method to `Database` class**

Open `db/database.py` and add this method after `get_latest_prices`:

```python
def list_parts(
    self,
    *,
    category: Optional[str] = None,
    source: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    sort: str = "price_asc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """
    Return (items, total) for the market listing page.
    Items have the latest price per part. NULL-price rows sort last.
    """
    conditions: list[str] = []
    params: list = []

    if category:
        conditions.append("p.category = ?")
        params.append(category)
    if source:
        conditions.append("p.source = ?")
        params.append(source)
    if min_price is not None:
        conditions.append("pl.price_pkr >= ?")
        params.append(min_price)
    if max_price is not None:
        conditions.append("pl.price_pkr <= ?")
        params.append(max_price)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    order = (
        "ORDER BY pl.price_pkr IS NULL, pl.price_pkr ASC"
        if sort == "price_asc"
        else "ORDER BY pl.price_pkr IS NULL, pl.price_pkr DESC"
    )

    base_query = f"""
        FROM parts p
        JOIN price_log pl ON pl.id = (
            SELECT id FROM price_log
            WHERE part_id = p.id
            ORDER BY scraped_at DESC
            LIMIT 1
        )
        {where}
    """

    total = self._conn.execute(
        f"SELECT COUNT(*) {base_query}", params
    ).fetchone()[0]

    rows = self._conn.execute(
        f"""
        SELECT p.id, p.source, p.name, p.category, p.url, p.thumbnail_url,
               pl.price_pkr
        {base_query}
        {order}
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    return [dict(r) for r in rows], total
```

- [ ] **Step 2: Commit**

```bash
git add db/database.py
git commit -m "feat(db): add list_parts query with filters and pagination"
```

---

## Task 2: Add `GET /api/parts` FastAPI endpoint

**Files:**
- Modify: `backend/routers/parts.py`

- [ ] **Step 1: Replace contents of `backend/routers/parts.py`**

```python
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from db.database import get_db

router = APIRouter(prefix="/api")

DB_PATH = Path(__file__).parent.parent.parent / "data" / "ppc.db"

VALID_CATEGORIES = {
    "gpu", "cpu", "ram", "ssd", "hdd", "psu",
    "case", "motherboard", "cooling", "monitor",
}
VALID_SOURCES = {
    "czone.com.pk", "zahcomputers.pk", "amdhouse.pk",
    "rbtechngames.com", "junaidtech.pk",
}


class PartItem(BaseModel):
    id: int
    source: str
    name: str
    category: str
    url: str
    thumbnail_url: Optional[str]
    price_pkr: Optional[int]


class PartsResponse(BaseModel):
    items: list[PartItem]
    total: int


@router.get("/stats")
def get_stats():
    with get_db(DB_PATH) as db:
        return db.stats()


@router.get("/parts", response_model=PartsResponse)
def get_parts(
    category: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    sort: str = Query("price_asc", pattern="^price_(asc|desc)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    # Silently ignore invalid enum values rather than 422ing
    if category not in VALID_CATEGORIES:
        category = None
    if source not in VALID_SOURCES:
        source = None

    with get_db(DB_PATH) as db:
        items, total = db.list_parts(
            category=category,
            source=source,
            min_price=min_price,
            max_price=max_price,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    return {"items": items, "total": total}
```

- [ ] **Step 2: Verify the endpoint works**

Start the backend: `cd /home/azaan/Documents/PPC && uvicorn backend.main:app --reload`

Then in another terminal:
```bash
curl "http://localhost:8000/api/parts?limit=3" | python3 -m json.tool
```
Expected: JSON with `items` array and `total` integer.

- [ ] **Step 3: Commit**

```bash
git add backend/routers/parts.py
git commit -m "feat(api): add GET /api/parts endpoint with filtering and sorting"
```

---

## Task 3: Add `getParts` to the frontend API layer

**Files:**
- Modify: `frontend/lib/api.ts`

- [ ] **Step 1: Extend `frontend/lib/api.ts`**

Replace the entire file with:

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Stats {
  total_parts: number;
  total_price_rows: number;
  by_source: Record<string, number>;
  by_category: Record<string, number>;
}

export async function getStats(): Promise<Stats | null> {
  try {
    const res = await fetch(`${API_BASE}/api/stats`, { next: { revalidate: 60 } });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export interface Part {
  id: number;
  source: string;
  name: string;
  category: string;
  url: string;
  thumbnail_url: string | null;
  price_pkr: number | null;
}

export interface PartsResult {
  items: Part[];
  total: number;
}

export interface PartsParams {
  category?: string;
  source?: string;
  min_price?: number;
  max_price?: number;
  sort?: "price_asc" | "price_desc";
  limit?: number;
  offset?: number;
}

export async function getParts(params: PartsParams = {}): Promise<PartsResult> {
  const query = new URLSearchParams();
  if (params.category)  query.set("category",  params.category);
  if (params.source)    query.set("source",    params.source);
  if (params.min_price) query.set("min_price", String(params.min_price));
  if (params.max_price) query.set("max_price", String(params.max_price));
  if (params.sort)      query.set("sort",      params.sort);
  if (params.limit)     query.set("limit",     String(params.limit));
  if (params.offset)    query.set("offset",    String(params.offset));

  try {
    const res = await fetch(`${API_BASE}/api/parts?${query}`, {
      next: { revalidate: 30 },
    });
    if (!res.ok) return { items: [], total: 0 };
    return res.json();
  } catch {
    return { items: [], total: 0 };
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "feat(frontend): add Part type and getParts() to API layer"
```

---

## Task 4: Build `PartRow` component

**Files:**
- Create: `frontend/components/PartRow.tsx`

- [ ] **Step 1: Create `frontend/components/PartRow.tsx`**

```tsx
import Image from "next/image";
import { Cpu, HardDrive, MemoryStick, MonitorPlay, Zap, Box, CircuitBoard, Wind, Database, Monitor } from "lucide-react";
import type { Part } from "@/lib/api";

const CATEGORY_ICONS: Record<string, React.ElementType> = {
  gpu:         MonitorPlay,
  cpu:         Cpu,
  ram:         MemoryStick,
  ssd:         Database,
  hdd:         HardDrive,
  psu:         Zap,
  case:        Box,
  motherboard: CircuitBoard,
  cooling:     Wind,
  monitor:     Monitor,
};

const SOURCE_SHORT: Record<string, string> = {
  "czone.com.pk":      "CZone",
  "zahcomputers.pk":   "Zah",
  "amdhouse.pk":       "AMD House",
  "rbtechngames.com":  "RB Tech",
  "junaidtech.pk":     "Junaid Tech",
};

function formatPrice(p: number | null): string {
  if (p === null) return "Out of stock";
  return "Rs " + p.toLocaleString("en-PK");
}

export default function PartRow({ part }: { part: Part }) {
  const Icon = CATEGORY_ICONS[part.category] ?? Cpu;

  return (
    <a
      href={part.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex items-center gap-4 px-4 py-3 no-underline transition-colors"
      style={{
        borderBottom: "1px solid var(--border)",
        background: "var(--bg-card)",
      }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLAnchorElement).style.background = "var(--bg-card-2)";
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLAnchorElement).style.background = "var(--bg-card)";
      }}
    >
      {/* Purple left accent stripe — appears on hover */}
      <div
        className="absolute left-0 top-0 bottom-0 w-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
        style={{ background: "var(--purple)" }}
      />

      {/* Thumbnail */}
      <div
        className="flex-shrink-0 w-12 h-12 rounded-lg flex items-center justify-center overflow-hidden"
        style={{ background: "var(--bg-section)", border: "1px solid var(--border)" }}
      >
        {part.thumbnail_url ? (
          <Image
            src={part.thumbnail_url}
            alt={part.name}
            width={48}
            height={48}
            className="object-contain"
            unoptimized
          />
        ) : (
          <Icon size={20} style={{ color: "var(--text-dim)" }} />
        )}
      </div>

      {/* Name + badges */}
      <div className="flex-1 min-w-0">
        <p
          className="font-medium text-sm truncate group-hover:text-[var(--purple)] transition-colors"
          style={{ color: "var(--text)" }}
        >
          {part.name}
        </p>
        <div className="flex items-center gap-2 mt-1">
          {/* Category tag */}
          <span
            className="mono px-1.5 py-px rounded-sm"
            style={{
              fontSize: "0.6rem",
              color: "var(--purple)",
              background: "var(--purple-muted)",
              border: "1px solid var(--border-purple)",
            }}
          >
            {part.category.toUpperCase()}
          </span>
          {/* Source badge */}
          <span
            className="mono"
            style={{ fontSize: "0.65rem", color: "var(--text-dim)" }}
          >
            {SOURCE_SHORT[part.source] ?? part.source}
          </span>
        </div>
      </div>

      {/* Price */}
      <div className="flex-shrink-0 text-right">
        <span
          className="font-bold mono"
          style={{
            fontSize: "0.95rem",
            color: part.price_pkr ? "var(--text)" : "var(--text-dim)",
          }}
        >
          {formatPrice(part.price_pkr)}
        </span>
      </div>
    </a>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/PartRow.tsx
git commit -m "feat(frontend): add PartRow list item component"
```

---

## Task 5: Build `FilterBar` client component

**Files:**
- Create: `frontend/components/FilterBar.tsx`

- [ ] **Step 1: Create `frontend/components/FilterBar.tsx`**

```tsx
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

const CATEGORIES = [
  "gpu", "cpu", "ram", "ssd", "hdd",
  "psu", "case", "motherboard", "cooling", "monitor",
];

const SOURCES = [
  { key: "czone.com.pk",     label: "CZone" },
  { key: "zahcomputers.pk",  label: "Zah Computers" },
  { key: "amdhouse.pk",      label: "AMD House" },
  { key: "rbtechngames.com", label: "RB Tech" },
  { key: "junaidtech.pk",    label: "Junaid Tech" },
];

const selectStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: "6px",
  padding: "6px 10px",
  fontSize: "0.78rem",
  fontFamily: '"JetBrains Mono", monospace',
  letterSpacing: "0.02em",
  cursor: "pointer",
  outline: "none",
  minWidth: "130px",
};

export default function FilterBar({ total }: { total: number }) {
  const router = useRouter();
  const params = useSearchParams();

  const push = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
      next.delete("offset"); // reset pagination on filter change
      router.push(`/market?${next.toString()}`);
    },
    [params, router]
  );

  const category  = params.get("category") ?? "";
  const source    = params.get("source")   ?? "";
  const sort      = params.get("sort")     ?? "price_asc";
  const minPrice  = params.get("min_price") ?? "";
  const maxPrice  = params.get("max_price") ?? "";

  return (
    <div
      className="sticky top-[52px] z-40 border-b"
      style={{
        background: "rgba(244,244,245,0.92)",
        borderColor: "var(--border)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
      }}
    >
      <div className="max-w-6xl mx-auto px-6 py-3 flex flex-wrap items-center gap-3">
        {/* Part count */}
        <span className="mono mr-2" style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>
          {total.toLocaleString()} parts
        </span>

        {/* Category */}
        <select
          value={category}
          onChange={e => push("category", e.target.value)}
          style={selectStyle}
        >
          <option value="">All Categories</option>
          {CATEGORIES.map(c => (
            <option key={c} value={c}>{c.toUpperCase()}</option>
          ))}
        </select>

        {/* Source */}
        <select
          value={source}
          onChange={e => push("source", e.target.value)}
          style={selectStyle}
        >
          <option value="">All Retailers</option>
          {SOURCES.map(s => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>

        {/* Price range */}
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            placeholder="Min PKR"
            value={minPrice}
            onChange={e => push("min_price", e.target.value)}
            style={{ ...selectStyle, minWidth: "90px", width: "90px" }}
          />
          <span className="mono" style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>—</span>
          <input
            type="number"
            placeholder="Max PKR"
            value={maxPrice}
            onChange={e => push("max_price", e.target.value)}
            style={{ ...selectStyle, minWidth: "90px", width: "90px" }}
          />
        </div>

        {/* Sort */}
        <select
          value={sort}
          onChange={e => push("sort", e.target.value)}
          style={{ ...selectStyle, marginLeft: "auto" }}
        >
          <option value="price_asc">Price: Low → High</option>
          <option value="price_desc">Price: High → Low</option>
        </select>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/FilterBar.tsx
git commit -m "feat(frontend): add FilterBar client component with URL-driven filters"
```

---

## Task 6: Build the Market page

**Files:**
- Create: `frontend/app/market/page.tsx`

- [ ] **Step 1: Create `frontend/app/market/page.tsx`**

```tsx
import { Suspense } from "react";
import Navbar from "@/components/Navbar";
import FilterBar from "@/components/FilterBar";
import PartRow from "@/components/PartRow";
import { getParts } from "@/lib/api";

interface PageProps {
  searchParams: Promise<Record<string, string>>;
}

async function PartsList({ searchParams }: { searchParams: Record<string, string> }) {
  const { items, total } = await getParts({
    category:  searchParams.category,
    source:    searchParams.source,
    min_price: searchParams.min_price ? Number(searchParams.min_price) : undefined,
    max_price: searchParams.max_price ? Number(searchParams.max_price) : undefined,
    sort:      (searchParams.sort as "price_asc" | "price_desc") ?? "price_asc",
    limit:     50,
    offset:    searchParams.offset ? Number(searchParams.offset) : 0,
  });

  return (
    <>
      {/* Sticky filter bar — needs total from server */}
      <Suspense>
        <FilterBar total={total} />
      </Suspense>

      <div className="max-w-6xl mx-auto px-6 py-6 w-full">
        {/* Section header */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="section-label mb-1">Browse Parts</p>
            <h1
              className="font-bold"
              style={{ fontSize: "clamp(1.2rem, 2.5vw, 1.6rem)", color: "var(--text)" }}
            >
              {searchParams.category
                ? searchParams.category.toUpperCase() + "s"
                : "All Parts"}
            </h1>
          </div>
        </div>

        {/* Parts list */}
        <div
          className="rounded-xl overflow-hidden relative"
          style={{ border: "1px solid var(--border)" }}
        >
          {/* Purple top accent stripe */}
          <div
            className="h-px w-full"
            style={{ background: "linear-gradient(90deg, var(--purple), transparent 60%)" }}
          />

          {items.length === 0 ? (
            <div
              className="py-20 text-center"
              style={{ background: "var(--bg-card)", color: "var(--text-dim)" }}
            >
              <p className="mono" style={{ fontSize: "0.8rem" }}>NO PARTS FOUND</p>
              <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
                Try adjusting your filters
              </p>
            </div>
          ) : (
            items.map(part => <PartRow key={part.id} part={part} />)
          )}
        </div>

        {/* Pagination hint */}
        {total > 50 && (
          <p className="mono mt-4 text-center" style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>
            Showing 50 of {total.toLocaleString()} parts — more pagination coming soon
          </p>
        )}
      </div>
    </>
  );
}

export default async function MarketPage({ searchParams }: PageProps) {
  const resolvedParams = await searchParams;

  return (
    <div className="flex flex-col flex-1" style={{ background: "var(--bg)" }}>
      <Navbar />
      <main className="flex flex-col flex-1">
        <Suspense fallback={<div className="py-20 text-center mono" style={{ color: "var(--text-dim)" }}>Loading…</div>}>
          <PartsList searchParams={resolvedParams} />
        </Suspense>
      </main>
      <footer
        className="border-t px-6 py-6 text-center text-xs"
        style={{
          background: "var(--bg)",
          borderColor: "var(--border)",
          color: "var(--text-dim)",
        }}
      >
        PakPC — prices updated regularly from Pakistani retailers
      </footer>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/market/page.tsx
git commit -m "feat(frontend): add market page with server-side filtering"
```

---

## Task 7: Enable Market nav link + enable "Browse Market" hero CTA

**Files:**
- Modify: `frontend/components/Navbar.tsx` (line 39)
- Modify: `frontend/components/Hero.tsx` (find the Browse Market button)

- [ ] **Step 1: Enable Market link in Navbar**

In `frontend/components/Navbar.tsx`, change line 39:
```tsx
// Before:
<NavLink href="/market" disabled>Market</NavLink>
// After:
<NavLink href="/market">Market</NavLink>
```

- [ ] **Step 2: Enable Browse Market CTA in Hero**

In `frontend/components/Hero.tsx`, find the "Browse Market" button/link (currently disabled or `href="#"`). Change it to:
```tsx
<Link href="/market" className="btn-primary ...">Browse Market →</Link>
```
(Keep existing className/style, just make it a real Link and remove any disabled state.)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/Navbar.tsx frontend/components/Hero.tsx
git commit -m "feat: enable Market nav link and Browse Market hero CTA"
```

---

## Task 8: End-to-end verification

- [ ] **Step 1: Start backend**

```bash
cd /home/azaan/Documents/PPC
uvicorn backend.main:app --reload
```

- [ ] **Step 2: Start frontend**

```bash
cd /home/azaan/Documents/PPC/frontend
npm run dev
```

- [ ] **Step 3: Open browser and verify**

Navigate to `http://localhost:3000/market`. Verify:
- Parts list renders with thumbnails (or icon placeholders)
- Category dropdown filters the list (URL updates, page re-renders)
- Source dropdown filters by retailer
- Min/Max price inputs filter by price range
- Sort toggle changes price ordering
- Clicking a part name opens the retailer URL in a new tab
- Purple left stripe appears on row hover
- Market link in navbar is active and navigates correctly
- "Browse Market" button on landing page navigates to `/market`
