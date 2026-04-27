"""
redtech.pk prebuilt scraper — WooCommerce

Category pages:
  /custom-pc-packages/       — primary prebuilt listing
  /shop/                     — full shop (parts + prebuilts; filtered by category tag)

RedTech product pages have a description section with component specs listed
as <strong>Label:</strong> Value or as a table. Variations (GPU options) are
parsed from the WooCommerce variation JSON embedded in the page.
"""

import html as html_module
import json
import re
import sys
import time

from scrapers.prebuilts.base_prebuilt_scraper import BasePrebuiltScraper

SOURCE  = "redtech.pk"
BASE    = "https://redtech.pk"

# RedTech organises prebuilts under /custom-pc-packages/ and a WooCommerce
# product category. Try both entry points.
CAT_URLS = [
    BASE + "/product-category/packages/",
]

_PLACEHOLDER = "woocommerce-placeholder"

_LABEL_MAP = {
    "cpu":            "cpu",
    "processor":      "cpu",
    "gpu":            "gpu",
    "graphics card":  "gpu",
    "graphic card":   "gpu",
    "graphics":       "gpu",
    "ram":            "ram",
    "memory":         "ram",
    "storage":        "storage",
    "ssd":            "storage",
    "hdd":            "storage",
    "hard drive":     "storage",
    "nvme":           "storage",
    "motherboard":    "motherboard",
    "mobo":           "motherboard",
    "psu":            "psu",
    "power supply":   "psu",
    "case":           "case",
    "casing":         "case",
    "cabinet":        "case",
    "cpu cooler":     "cpu_cooler",
    "cooler":         "cpu_cooler",
    "cooling":        "cpu_cooler",
    "os":             "os",
    "operating system": "os",
}


class RedTechScraper(BasePrebuiltScraper):

    def scrape_all(self) -> list[dict]:
        all_links: list[str] = []
        seen_links: set[str] = set()

        for cat_url in CAT_URLS:
            page = 1
            while True:
                url = cat_url if page == 1 else f"{cat_url}page/{page}/"
                print(f"  [redtech] page {page}: {url}")
                try:
                    html = self.fetch(url)
                except RuntimeError as e:
                    print(f"  [redtech] fetch error: {e}")
                    break

                links = self._extract_product_links(html)
                if not links:
                    print(f"  [redtech] no products on page {page} — done.")
                    break

                new = [l for l in links if l not in seen_links]
                for l in new:
                    seen_links.add(l)
                all_links.extend(new)

                # Check for next page
                if f'page/{page + 1}' not in html and 'next' not in html.lower():
                    break
                page += 1
                time.sleep(self.PAGE_DELAY)

        print(f"  [redtech] {len(all_links)} unique product URLs found")

        results: list[dict] = []
        for product_url in all_links:
            print(f"    -> {product_url}")
            try:
                p_html = self.fetch(product_url)
                item = self._parse_product(p_html, product_url)
                if item:
                    results.append(item)
            except Exception as e:
                print(f"    ERROR: {e}")
            time.sleep(self.PAGE_DELAY)

        return results

    def _extract_product_links(self, html: str) -> list[str]:
        links = re.findall(
            r'href="(https://redtech\.pk/product/[^"?#]+)"',
            html,
        )
        seen: set[str] = set()
        result = []
        for l in links:
            if l not in seen:
                seen.add(l)
                result.append(l)
        return result

    def _parse_product(self, html: str, url: str) -> dict | None:
        scraped_at = self.now()

        # Title
        title_m = re.search(
            r'class="[^"]*product_title[^"]*"[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE
        )
        if not title_m:
            title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if not title_m:
            return None
        name = html_module.unescape(re.sub(r"<[^>]+>", "", title_m.group(1)).strip())

        # Skip non-prebuilt products (parts, accessories, monitors)
        # RedTech shop has GPUs, processors etc. — skip those.
        skip_keywords = ("graphic card", "graphics card", "processor", "monitor", "keyboard",
                         "mouse", "headset", "speaker", "chair", "desk", "power supply unit",
                         "motherboard only", "ram only", "ssd only")
        name_lower = name.lower()
        if any(kw in name_lower for kw in skip_keywords):
            return None

        # Price — use JSON-LD offer price (most reliable across regular + variable products)
        price: int | None = None
        price_m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if price_m:
            try:
                price = int(float(price_m.group(1)))
            except ValueError:
                pass
        # Fallback: WooCommerce standard price span
        if price is None:
            reg_m = re.search(
                r'woocommerce-Price-amount amount">\s*<bdi>\s*<span[^>]+>[^<]+</span>([\d,]+)',
                html,
            )
            if reg_m:
                price = int(reg_m.group(1).replace(",", ""))

        # Thumbnail
        thumb: str | None = None
        og_m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if og_m and _PLACEHOLDER not in og_m.group(1):
            thumb = og_m.group(1)

        components = self._extract_components(html)

        # Heuristic: if no components extracted, likely not a prebuilt — skip
        if not components:
            return None

        return {
            "name":          name,
            "price_pkr":     price,
            "url":           url,
            "source":        SOURCE,
            "scraped_at":    scraped_at,
            "thumbnail_url": thumb,
            "components":    components,
        }

    def _extract_components(self, html: str) -> dict:
        components: dict[str, str] = {}

        # Strategy 1: HTML table rows — <tr><td>Label</td><td>Value</td><td>New/Used</td></tr>
        # RedTech uses a 3-column spec table (component, model, new/used condition)
        table_rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*>\s*([^<]{2,50}?)\s*</td>\s*<td[^>]*>\s*([^<]{2,300}?)\s*</td>',
            html, re.IGNORECASE
        )
        for label_raw, value_raw in table_rows:
            label = re.sub(r"<[^>]+>", "", label_raw).strip().lower().rstrip(":")
            value = re.sub(r"<[^>]+>", "", value_raw).strip()
            if not value or value == "—":
                continue
            key = _LABEL_MAP.get(label)
            if key and key not in components:
                components[key] = value

        # Strategy 2: <strong>Label:</strong> Value pattern in description
        if not components:
            strong_pairs = re.findall(
                r'<strong>\s*([^<]{2,50}?)\s*:?\s*</strong>\s*(?:<br\s*/?>|:)?\s*([^<\n]{3,200})',
                html, re.IGNORECASE
            )
            for label_raw, value_raw in strong_pairs:
                label = label_raw.strip().lower().rstrip(":")
                value = re.sub(r"<[^>]+>", "", value_raw).strip().lstrip(":").strip()
                if not value:
                    continue
                key = _LABEL_MAP.get(label)
                if key and key not in components:
                    components[key] = value

        return components


def main():
    scraper = RedTechScraper()
    results = scraper.scrape_all()
    print(f"\n[redtech] scraped {len(results)} prebuilts")
    for r in results[:3]:
        print(f"  {r['name']} — Rs.{r['price_pkr']:,}" if r['price_pkr'] else f"  {r['name']} — no price")
        if r["components"]:
            for k, v in r["components"].items():
                print(f"    {k}: {v}")
    if results:
        scraper.write_to_db(results)


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    main()
