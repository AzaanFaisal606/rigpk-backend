# HTML fixtures

Saved retailer pages, used by the parser tests. Never fetched at test time.

Naming: `<source>_<category>_<what>.html`, e.g. `zah_gpu_page1.html`,
`redtech_prebuilt_instock.html`. JSON API fixtures live in `../json/`.

To add or refresh one:

    python scripts/save_fixture.py "https://…" zah_gpu_page1.html

Fixtures are committed. They are the only record of what a retailer's markup
looked like when a parser was written, and a diff on one is the fastest way to
see what a site changed.

Trim nothing by hand — a hand-edited fixture stops being evidence. The one
exception is secrets: `czone_motherboard_page1.html` has its webx bearer/refresh
tokens replaced with `REDACTED…` (the tests expect that exact string).
