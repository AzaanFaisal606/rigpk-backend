"""
zestrogaming.com prebuilt scraper — WooCommerce

Category page:  /product-category/gaming-pc-price-in-pakistan/page/N/
Product pages:  /product/<slug>/

Spec extraction strategy:
  The product description contains a "Full Specifications" section with
  <strong>Label</strong>\nValue pairs. We parse those to build the
  components dict. Fallback: parse the summary bullet list (icon + text).
"""

import html as html_module
import re
import sys
import time

from scrapers.prebuilts.base_prebuilt_scraper import BasePrebuiltScraper

SOURCE = "zestrogaming.com"
BASE   = "https://zestrogaming.com"
CAT_URL = BASE + "/product-category/gaming-pc-price-in-pakistan/"

# Maps label text (lowercased) found in the Full Specifications section
# to our normalised component key.
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
    "case fans":      "case_fans",
    "fans":           "case_fans",
    "os":             "os",
    "operating system": "os",
}

_PLACEHOLDER = "woocommerce-placeholder"


class ZestroScraper(BasePrebuiltScraper):

    def scrape_all(self) -> list[dict]:
        all_products: list[dict] = []
        page = 1
        while True:
            url = CAT_URL if page == 1 else f"{CAT_URL}page/{page}/"
            print(f"  [zestro] category page {page}: {url}")
            html = self.fetch(url)

            links = self._extract_product_links(html)
            if not links:
                print(f"  [zestro] no products on page {page} — done.")
                break

            for product_url in links:
                print(f"    -> {product_url}")
                try:
                    p_html = self.fetch(product_url)
                    item = self._parse_product(p_html, product_url)
                    if item:
                        all_products.append(item)
                except Exception as e:
                    print(f"    ERROR: {e}")
                time.sleep(self.PAGE_DELAY)

            page += 1
            time.sleep(self.PAGE_DELAY)

        return all_products

    def _extract_product_links(self, html: str) -> list[str]:
        # WooCommerce product links: <a class="woocommerce-loop-product__link" href="...">
        # Also match plain /product/ links in the listing.
        links = re.findall(
            r'href="(https://zestrogaming\.com/product/[^"?#]+)"',
            html,
        )
        # Deduplicate preserving order
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

        # Price — JSON-LD offer price is most reliable
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

        # Thumbnail — og:image is reliable on WooCommerce product pages
        thumb: str | None = None
        og_m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if og_m and _PLACEHOLDER not in og_m.group(1):
            thumb = og_m.group(1)

        # Components — parse "Full Specifications" section
        components = self._extract_components(html)

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

        # Strategy 1: Full Specifications section — <strong>Label</strong>text pairs.
        # Zestro uses Elementor with a section titled "Full Specifications" containing
        # <strong>CPU</strong>Core i5 12400f style inline pairs inside <p> tags.
        idx = html.find("Full Spec")
        if idx >= 0:
            chunk = html[idx:idx + 8000]
            pairs = re.findall(
                r'<strong>(.*?)</strong>(.*?)(?=<strong>|</p>|<br|</div>)',
                chunk, re.DOTALL
            )
            for label_raw, value_raw in pairs:
                label = re.sub(r"<[^>]+>", "", label_raw).strip().lower().rstrip(":")
                value = re.sub(r"<[^>]+>", "", value_raw).strip()
                if not value:
                    continue
                key = _LABEL_MAP.get(label)
                if key and key not in components:
                    components[key] = value

        # Strategy 2: Short description bullet list — icon img src encodes component type.
        # <li><img src=".../icon-cpu-80x80.png"/> Core i5 12400f Processor</li>
        if not components:
            li_items = re.findall(
                r'<li[^>]*>.*?<img[^>]+src="([^"]*icon-([a-z]+)[^"]*)"[^>]*/?>(.{3,200}?)</li>',
                html, re.DOTALL | re.IGNORECASE
            )
            for _src, icon_type, text_raw in li_items:
                value = re.sub(r"<[^>]+>", "", text_raw).strip()
                key = _LABEL_MAP.get(icon_type)
                if key and key not in components and value:
                    components[key] = value

        return components


def main():
    scraper = ZestroScraper()
    results = scraper.scrape_all()
    print(f"\n[zestro] scraped {len(results)} prebuilts")
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
