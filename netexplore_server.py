#!/usr/bin/env python3
"""NetExplore Server — PICO-8 web proxy via cartdata bridge"""

import time
import requests
import re
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin, quote_plus, urlparse, parse_qs
from netexplore_bridge import send, receive, TLIST

POLL_INTERVAL = 1/15
MAX_COLS = 32  # 128px / 4px per char
SEARCH_ENGINE = "https://html.duckduckgo.com/html/?q="

VALID_CHARS = set(TLIST)

UNICODE_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201C": '"', "\u201D": '"',
    "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2026": "...", "\u00A0": " ",
    "\u00AB": '"', "\u00BB": '"',
    "\u2022": "*", "\u00B7": "*",
    "\u00A9": "(c)", "\u00AE": "(R)", "\u2122": "(TM)",
    "\u00D7": "x", "\u00F7": "/",
}

BLOCK_TAGS = {
    "div", "p", "ul", "ol", "li", "table",
    "pre", "section", "article", "main",
    "aside", "figure", "figcaption", "dl", "dt", "dd",
    "details", "summary", "hr",
}

DDG_NOISE = {
    "web results are present",
    "this is the visible part",
    "links wrapper //",
    "abstract already shown above for no-results, skip here",
    "if zero click results are present",
}


def normalize_text(s):
    for uc, repl in UNICODE_MAP.items():
        s = s.replace(uc, repl)
    return "".join(c for c in s if c in VALID_CHARS)


def wait_for_request():
    # read current state so we don't trigger on stale requests
    _, initial = receive()
    last_text = initial.strip()
    while True:
        cmd, text = receive()
        if cmd == "fetch" and text.strip() != last_text.strip() and text.strip():
            last_text = text.strip()
            return last_text
        time.sleep(POLL_INTERVAL)


def send_page(lines):
    send("SOF")
    time.sleep(POLL_INTERVAL * 2)
    for line in lines:
        send(line)
        time.sleep(POLL_INTERVAL)
    time.sleep(POLL_INTERVAL * 2)
    send("EOF")
    time.sleep(POLL_INTERVAL)


def is_url(text):
    t = text.strip().lower()
    if t.startswith("http://") or t.startswith("https://"):
        return True
    if "." in t and " " not in t:
        return True
    return False


def normalize_url(url):
    url = url.strip()
    if not url.lower().startswith("http://") and not url.lower().startswith("https://"):
        url = "https://" + url
    return url


def unwrap_redirect(href):
    parsed = urlparse(href)
    if parsed.hostname and "duckduckgo.com" in parsed.hostname:
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


def fetch_page(url):
    """Fetch URL, return list of element dicts"""
    try:
        headers = {"User-Agent": "NetExplore/0.5 (PICO-8)"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        return [{"type": "text", "text": f"ERROR: {str(e)[:50]}"}]

    # handle non-HTML content types
    ctype = resp.headers.get("Content-Type", "")
    if ctype.startswith("image/"):
        return [{"type": "img", "src": url}]
    if not any(ctype.startswith(t) for t in ("text/html", "application/xhtml", "")):
        return [{"type": "text", "text": f"CANNOT DISPLAY: {ctype[:30]}"}]

    soup = BeautifulSoup(resp.text, "html.parser")

    # detect background color from body style or <style> tags
    bg_color = None
    # check inline style on body
    body = soup.find("body")
    bg_sources = []
    if body:
        bg_sources.append(body.get("style", ""))
    # check <style> tags for body background
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            bg_sources.append(style_tag.string)
    for style_text in bg_sources:
        bg_match = re.search(r'background(?:-color)?\s*:\s*#([0-9a-fA-F]{3,6})', style_text)
        if bg_match:
            hex_col = bg_match.group(1)
            if len(hex_col) == 3:
                hex_col = hex_col[0]*2 + hex_col[1]*2 + hex_col[2]*2
            r, g, b = int(hex_col[0:2],16), int(hex_col[2:4],16), int(hex_col[4:6],16)
            bg_color = closest_color(r, g, b)
            break

    for tag in soup(["script", "style", "svg", "canvas", "video", "audio",
                     "iframe", "head", "meta", "link", "template", "nav",
                     "noscript", "select", "option", "form", "button", "input"]):
        tag.decompose()

    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()
    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()
    for tag in soup.find_all(class_=lambda c: c and any(
        x in str(c).lower() for x in ["sr-only", "visually-hidden", "hidden", "display-none",
                                       "jump-link", "skip-link", "skip-nav",
                                       "mw-editsection", "navbox", "catlinks",
                                       "sidebar", "infobox", "mw-jump-link",
                                       "noprint", "mw-indicators"]
    )):
        tag.decompose()
    for tag in soup.find_all(id=lambda i: i and i in [
        "toc", "mw-navigation", "mw-panel", "p-lang", "p-tb",
        "footer", "catlinks", "jump-to-nav"
    ]):
        tag.decompose()
    for tag in soup.find_all("sup", class_="reference"):
        tag.decompose()

    elements = []

    def walk(node, current_href=None):
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, str):
                text = child.strip()
                if not text or text.lower() in DDG_NOISE:
                    continue
                text = normalize_text(text)
                if not text:
                    continue
                elem = {"type": "text", "text": text}
                if current_href:
                    elem = {"type": "link", "text": text, "href": current_href}
                elements.append(elem)
            elif hasattr(child, "name"):
                if child.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    elements.append({"type": "break"})
                    walk(child, current_href)
                    elements.append({"type": "break"})
                elif child.name == "hr":
                    elements.append({"type": "break"})
                    elements.append({"type": "div"})
                    elements.append({"type": "break"})
                elif child.name in BLOCK_TAGS:
                    elements.append({"type": "break"})
                    walk(child, current_href)
                    elements.append({"type": "break"})
                elif child.name == "a" and child.get("href"):
                    href = child["href"]
                    if not href.startswith("http"):
                        href = urljoin(url, href)
                    href = unwrap_redirect(href)
                    walk(child, href)
                elif child.name == "img":
                    src = child.get("src") or child.get("data-src") or ""
                    # skip SVGs and tiny tracking pixels
                    if src and not src.endswith(".svg") and "1x1" not in src:
                        if src.startswith("//"):
                            src = "https:" + src
                        elif not src.startswith("http"):
                            src = urljoin(url, src)
                        elements.append({"type": "img", "src": src})
                elif child.name == "br":
                    elements.append({"type": "break"})
                else:
                    walk(child, current_href)

    if soup.body:
        walk(soup.body)
    else:
        walk(soup)

    if not any(e["type"] != "break" for e in elements):
        raw = soup.get_text(separator="\n")
        for line in raw.splitlines():
            line = normalize_text(line.strip())
            if line:
                elements.append({"type": "text", "text": line})

    if bg_color is not None:
        elements.insert(0, {"type": "bgc", "color": bg_color})

    return elements


PICO8_PALETTE = [
    (0,0,0), (29,43,83), (126,37,83), (0,135,81),
    (171,82,54), (95,87,79), (194,195,199), (255,241,232),
    (255,0,77), (255,163,0), (255,236,39), (0,228,54),
    (41,173,255), (131,118,156), (255,119,168), (255,204,170)
]


def closest_color(r, g, b):
    """Find closest PICO-8 palette color"""
    best = 0
    best_dist = float('inf')
    for i, (pr, pg, pb) in enumerate(PICO8_PALETTE):
        d = (r-pr)**2 + (g-pg)**2 + (b-pb)**2
        if d < best_dist:
            best_dist = d
            best = i
    return best


def image_to_tiles(img, x, y):
    """Convert PIL image to 8x8 IMG tile packets"""
    from PIL import Image
    packets = []
    w, h = img.size
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            hex_data = ""
            for py in range(8):
                for px in range(8):
                    ix, iy = tx + px, ty + py
                    if ix < w and iy < h:
                        r, g, b, a = img.getpixel((ix, iy))
                        if a < 128:
                            hex_data += "0"
                        else:
                            hex_data += format(closest_color(r, g, b), "X")
                    else:
                        hex_data += "0"
            packets.append(f"IMG|{x + tx}|{y + ty}|{hex_data}")
    return packets


LIGHT_COLORS = {6, 7, 9, 10, 11, 14, 15}  # colors that need dark text


def layout_page(elements):
    """Convert elements to positioned TXT/HYP/IMG/DIV packets with inline flow"""
    packets = []
    y = 0
    x = 0  # current x cursor in characters
    line_segs = []

    # detect bg color to choose text color
    bg = 0
    for elem in elements:
        if elem["type"] == "bgc":
            bg = elem["color"]
            break
    text_color = 0 if bg in LIGHT_COLORS else 7  # dark text on light bg, white on dark

    def flush_line():
        nonlocal y, x, line_segs
        for seg_x, seg_text, seg_color, seg_href in line_segs:
            px = seg_x * 4  # convert char position to pixels
            if seg_href:
                packets.append(f"HYP|{px}|{y}|{seg_text}|{seg_href}")
            else:
                packets.append(f"TXT|{px}|{y}|{seg_color}|{seg_text}")
        if line_segs:
            y += 6
        line_segs = []
        x = 0

    def add_inline(text, color=7, href=None):
        nonlocal x, line_segs
        words = text.split()
        for word in words:
            wlen = len(word)
            # need space before word?
            need_space = 1 if x > 0 else 0

            # does it fit on current line?
            if x + need_space + wlen > MAX_COLS and x > 0:
                # line full, flush and start new line
                flush_line()
                need_space = 0

            # add space if needed
            if need_space:
                # append space to last segment if same style, else start new
                if line_segs and line_segs[-1][2] == color and line_segs[-1][3] == href:
                    seg_x, seg_text, seg_color, seg_href = line_segs[-1]
                    line_segs[-1] = (seg_x, seg_text + " " + word, seg_color, seg_href)
                else:
                    # space goes with new segment
                    line_segs.append((x, " " + word, color, href))
                x += 1 + wlen
            else:
                # no space needed
                if line_segs and line_segs[-1][2] == color and line_segs[-1][3] == href:
                    seg_x, seg_text, seg_color, seg_href = line_segs[-1]
                    line_segs[-1] = (seg_x, seg_text + word, seg_color, seg_href)
                else:
                    line_segs.append((x, word, color, href))
                x += wlen
                cur_line = word

    # strip leading breaks, collapse consecutive breaks
    collapsed = []
    last_was_break = False
    found_content = False
    for elem in elements:
        if elem["type"] == "break":
            if found_content and not last_was_break:
                collapsed.append(elem)
            last_was_break = True
        else:
            found_content = True
            collapsed.append(elem)
            last_was_break = False
    elements = collapsed

    for elem in elements:
        if elem["type"] == "bgc":
            packets.insert(0, f"BGC|{elem['color']}")
        elif elem["type"] == "div":
            flush_line()
            packets.append(f"DIV|{y}")
            y += 4
        elif elem["type"] == "break":
            flush_line()
            y += 2
        elif elem["type"] == "text":
            add_inline(elem["text"], text_color)
        elif elem["type"] == "link":
            add_inline(elem["text"], 12, elem["href"])
        elif elem["type"] == "img":
            flush_line()
            try:
                from PIL import Image
                import io
                img_data = requests.get(elem["src"], timeout=10).content
                img = Image.open(io.BytesIO(img_data)).convert("RGBA")
                w, h = img.size
                max_dim = 32
                if w > max_dim or h > max_dim:
                    ratio = min(max_dim / w, max_dim / h)
                    w, h = int(w * ratio), int(h * ratio)
                    img = img.resize((w, h), Image.LANCZOS)
                tiles = image_to_tiles(img, 0, y)
                packets.extend(tiles)
                print(f"  Image: {elem['src'][:50]} -> {len(tiles)} tiles ({w}x{h})")
                y += h + 2
            except Exception as e:
                print(f"  Image failed: {elem['src'][:50]} -> {e}")

    flush_line()
    return packets


def handle_request(request):
    print(f"Request: '{request}'")

    if is_url(request):
        url = normalize_url(request)
        print(f"  Fetching URL: {url}")
    else:
        url = SEARCH_ENGINE + quote_plus(request)
        print(f"  Searching: {request}")

    elements = fetch_page(url)
    page = layout_page(elements)

    return page


if __name__ == "__main__":
    print("NetExplore server started. Waiting for requests...")
    while True:
        request = wait_for_request()
        page = handle_request(request)
        send_page(page)
        print(f"Page sent! ({len(page)} lines)")
