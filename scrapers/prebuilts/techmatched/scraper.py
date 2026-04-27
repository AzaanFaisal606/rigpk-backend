"""
techmatched.pk prebuilt scraper — WooCommerce

Category URL: /product-category/gaming-pc-prices-in-pakistan/
Paginated: /product-category/gaming-pc-prices-in-pakistan/page/N/

TechMatched product pages follow WooCommerce standard with a description
section containing component specs. Build names include price tier in the
title (e.g. "270K Best Gaming PC"). Products are named with build code
slugs like GV-2.7, GV-5.8, etc.
"""

import html as html_module
import re
import sys
import time

from scrapers.prebuilts.base_prebuilt_scraper import BasePrebuiltScraper

SOURCE  = "techmatched.pk"
BASE    = "https://techmatched.pk"
CAT_URL = BASE + "/product-category/gaming-pc-prices-in-pakistan/"

_PLACEHOLDER = "woocommerce-placeholder"

_LABEL_MAP = {
    "cpu":            "cpu",
    "processor":      "cpu",
    "gpu":            "gpu",
    "graphics card":  "gpu",
    "graphic card":   "gpu",
    "graphics":       "gpu",
    "vga":            "gpu",
    "ram":            "ram",
    "memory":         "ram",
    "storage":        "storage",
    "ssd":            "storage",
    "hdd":            "storage",
    "hard drive":     "storage",
    "nvme":           "storage",
    "m.2":            "storage",
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
    "windows":        "os",
}


class TechMatchedScraper(BasePrebuiltScraper):

    def scrape_all(self) -> list[dict]:
        all_links: list[str] = []
        seen_links: set[str] = set()
        page = 1

        while True:
            url = CAT_URL if page == 1 else f"{CAT_URL}page/{page}/"
            print(f"  [techmatched] page {page}: {url}")
            try:
                html = self.fetch(url)
            except RuntimeError as e:
                print(f"  [techmatched] fetch error: {e}")
                break

            links = self._extract_product_links(html)
            if not links:
                print(f"  [techmatched] no products on page {page} — done.")
                break

            new = [l for l in links if l not in seen_links]
            for l in new:
                seen_links.add(l)
            all_links.extend(new)

            print(f"    {len(new)} new links (total: {len(all_links)})")

            # Stop if no next-page link
            if not re.search(rf'/{page + 1}/', html):
                break
            page += 1
            time.sleep(self.PAGE_DELAY)

        print(f"  [techmatched] {len(all_links)} unique product URLs")

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
            r'href="(https://techmatched\.pk/product/[^"?#]+)"',
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

        # Skip non-prebuilt items (cases, peripherals, accessories)
        skip_keywords = ("gaming case", "keyboard", "mouse", "headset", "monitor",
                         "speaker", "mousepad", "chair", "desk", "rgb fan")
        if any(kw in name.lower() for kw in skip_keywords):
            return None

        # Price — JSON-LD offer price
        price: int | None = None
        price_m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if price_m:
            try:
                price = int(float(price_m.group(1)))
            except ValueError:
                pass
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

        # TechMatched uses table rows: "Label -> Value – New/Used"
        # e.g. "Processor -> Ryzen 5 7500F – New"
        table_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        for row in table_rows:
            text = re.sub(r"<[^>]+>", " ", row)
            text = re.sub(r"&gt;", ">", text)
            text = re.sub(r"&#\d+;|&[a-z]+;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            # Expect "Label -> Value – Condition" or "Label -> Value"
            arrow_m = re.match(r'^(.+?)\s*->\s*(.+?)(?:\s*[–\-]+\s*(?:New|Used|Refurbished))?$', text, re.IGNORECASE)
            if arrow_m:
                label = arrow_m.group(1).strip().lower().rstrip(":")
                value = arrow_m.group(2).strip()
                # Strip trailing " - New" / " – New" / " New" condition suffix
                value = re.sub(r'\s*[\-–]+\s*(New|Used|Refurbished)\s*$', '', value, flags=re.IGNORECASE).strip()
                value = re.sub(r'\s+(New|Used|Refurbished)\s*$', '', value, flags=re.IGNORECASE).strip()
                if not value or value == "—":
                    continue
                key = _LABEL_MAP.get(label)
                if key and key not in components:
                    components[key] = value

        # Fallback: <strong>Label</strong> Value pattern
        if not components:
            strong_pairs = re.findall(
                r'<(?:strong|b)>\s*([^<]{2,50}?)\s*:?\s*</(?:strong|b)>\s*(?:<br\s*/?>|:)?\s*([^<\n]{3,300})',
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
    scraper = TechMatchedScraper()
    results = scraper.scrape_all()
    print(f"\n[techmatched] scraped {len(results)} prebuilts")
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
