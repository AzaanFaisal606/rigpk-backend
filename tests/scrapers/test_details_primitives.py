from scrapers.details.clean import is_boilerplate, strip_boilerplate, truncate
from scrapers.details.html_tree import find_all, parse_html, text


def test_tolerates_stray_end_tags():
    root = parse_html("<table><tr><td>A</td><td>1</p><p>2</td></tr></table>")
    cells = find_all(root, "td")
    assert [text(c) for c in cells] == ["A", "1 2"]


def test_br_joiner():
    assert text(parse_html("<td>x<br/>y</td>"), br=" / ") == "x / y"


def test_text_unescapes_and_collapses():
    assert text(parse_html("<p>&nbsp;</p><p>A &#8211;  B&amp;C</p>")) == "A – B&C"


def test_scripts_and_comments_dropped():
    assert text(parse_html("<p>a<!-- c --><script>x</script>b</p>")).replace(" ", "") == "ab"


def test_unclosed_li():
    assert [text(n) for n in find_all(parse_html("<ul><li>a<li>b</ul>"), "li")] == ["a", "b"]


def test_nested_list_keeps_parent_li():
    root = parse_html("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>")
    assert [text(n) for n in find_all(root, "li")] == ["a b", "b", "c"]


def test_none_and_img():
    assert text(parse_html(None)) == ""
    assert text(parse_html("<p>x<img src='a.png'>y</p>")).replace(" ", "") == "xy"
    assert len(find_all(parse_html("<p><img src='a'></p>"), "img")) == 1


def test_boilerplate():
    assert is_boilerplate("Corsair CX750 Price in Pakistan", "redtech.pk")
    assert is_boilerplate("Available at Tech Arc.", "techarc.pk")
    assert is_boilerplate("TechMatched offers a price-match policy", "techmatched.pk")
    assert is_boilerplate("We will match the price for you.", "techmatched.pk")
    assert not is_boilerplate("AMD Ryzen 5 with 6 cores.", "amdhouse.pk")
    assert strip_boilerplate(
        "Great card. Available at AMD House with best price guarantee.", "amdhouse.pk"
    ) == "Great card."


def test_truncate_at_sentence():
    assert truncate("One two. Three four five.", 12) == "One two."


def test_truncate_at_space_and_short():
    assert truncate("short", 10) == "short"
    out = truncate("alpha beta gamma delta", 12)
    assert out.endswith("…") and len(out) <= 12


def test_pakistan_and_delivery_copy_is_boilerplate():
    assert is_boilerplate("Buy the KOORUI G2741L 27-inch monitor in Pakistan.", "techarc.pk")
    assert is_boilerplate("Free Delivery All Over Pakistan", "junaidtech.pk")
    assert is_boilerplate("Fulfilled by Xigmatek", "junaidtech.pk")
    assert strip_boilerplate("Buy the KOORUI G2741L in Pakistan. HDR400, HDMI 2.1.", "techarc.pk") == "HDR400, HDMI 2.1."


def test_zah_store_name_written_as_one_word_is_boilerplate():
    s = "Take your gaming to the next level with a powerful graphics card from ZahComputers."
    assert is_boilerplate(s, "zahcomputers.pk")
