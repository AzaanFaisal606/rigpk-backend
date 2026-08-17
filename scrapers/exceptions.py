"""
Shared exceptions for scraper orchestration.

Kept dependency-free (no imports from base_scraper or any scraper module) so
every scraper and run_all.py can import from here without risking a cycle.
"""


class ScrapeIncomplete(Exception):
    """
    A scrape ran but could not finish: pages failed, a loop bound was hit, a
    token could not be extracted, or the host blocked us mid-run.

    The distinction that matters is between "this source genuinely has fewer
    products now" and "we could not see all of them". Only the first should
    ever reach the freshness sweep. Returning a short list instead of raising
    is what produced the techmatched amputation (653 -> 254 -> 163).

    Orchestrators (run_all.py, run_prebuilts.py) may stash whatever products
    were collected before the fault on `partial_results` / `valid_categories`
    (a plain attribute, not a constructor arg) so a failure partway through
    one category doesn't discard data the other categories already collected.
    """
