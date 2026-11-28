"""Find a club's badge and colours online, to choose from.

Searched with the club's name plus whatever was added to narrow it down
("Seattle", "Washington State") - or a website, which is then read
directly. Sources, all asked at once:

* **Wikipedia** - the infobox of the best-matching articles: the logo /
  crest / seal files and the colours line ("Navy Blue, Red, and White",
  `{{color box|#002F6C}}`), plus their **Wikidata** entries: logo,
  seal, official colours (as exact hex) and official website. Schools
  and universities are mostly there; clubs mostly aren't.
* **Wikimedia Commons** - files named after the club with "logo".
* **The club's website** - typed into the hints, found on Wikidata or
  found by a web search: the images in its logo spot (an <img> called,
  or sitting in something called, "logo"), the logo its structured data
  names, the first images of its header, its icons, social-share image
  and the browser theme colour.
* **Web search** (Brave, only with an API key in VME_BRAVE_API_KEY,
  https://brave.com/search/api/) - step one of two: ordinary web results
  for the name and hints, from which the club's own site is picked
  (named like the club; not a directory, social network or search
  engine) and then read as above. Skipped when a website is typed into
  the hints. (DuckDuckGo would need no key but turns away anything that
  isn't a browser.)
* **Image search** (Brave, same key) - "<name> <hints> logo" across the
  web. Hit and miss, so its results come after the club's own site's.

Wikimedia is asked as little as it takes (a search brings the articles'
infoboxes with it) since it turns away clients that ask in bursts.

Every request says it's VME (no browser disguise) and only public
addresses are fetched - never the PC's own network - including after a
redirect. Found images are remembered by id for a while, so the browser
asks for "candidate 3fa2…" rather than handing VME any address to fetch.
Each candidate's address still goes to the browser, but only to show
where the image came from; nothing fetches by it.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests

from . import logo_tools

logger = logging.getLogger(__name__)

# Wikimedia asks for a way to reach whoever runs a client and is
# stricter with those that give none; VME_LOOKUP_CONTACT (an email or a
# web address) goes in when set.
UA = (f"VME-club-lookup/1.0 ({os.environ.get('VME_LOOKUP_CONTACT', '').strip() or 'self-hosted volleyball video tool'})"
      f" python-requests/{requests.__version__}")
TIMEOUT = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BRAVE_WEB = "https://api.search.brave.com/res/v1/web/search"
BRAVE_IMAGES = "https://api.search.brave.com/res/v1/images/search"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico")
# Pillow can't draw SVG; Wikimedia renders theirs to PNG for us, a
# website's we skip.
_LOGOISH = re.compile(r"logo|crest|seal|emblem|badge|wordmark|athletic|insignia|shield|mascot", re.I)
# Wikimedia's own icons, flags and maintenance badges on an article.
_JUNK = re.compile(r"^(commons|wiki\w*|wiktionary|wikiquote|wikisource|wikinews|wikiversity|wikivoyage|"
                   r"meta|mediawiki)[-_ ]logo|^flag[_ ]of|question[_ ]book|ambox|edit-clear|"
                   r"symbol[_ ]|padlock|disambig|portal|^office[_ ]logo", re.I)
# How names get shortened: "NW Juniors" for Northwest, "St." for Saint.
_SHORT = {"nw": "northwest", "ne": "northeast", "sw": "southwest", "se": "southeast", "n": "north",
          "s": "south", "e": "east", "w": "west", "st": "saint", "mt": "mount", "ft": "fort",
          "prep": "preparatory", "intl": "international", "jrs": "juniors", "acad": "academy"}
_STOP = {"the", "of", "and", "high", "school", "club", "volleyball", "vbc", "academy",
         "university", "college", "juniors", "junior", "jrs", "jr", "team", "sports"}
_LONG = {v: k for k, v in _SHORT.items() if len(k) > 1}
# Sites about clubs rather than of one: the logo on their pages is their
# own. (Hosts of club sites - sportngin, teamsnap, leagueapps - are fine.)
_NOT_A_CLUB = ("facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
               "linkedin.com", "pinterest.com", "reddit.com", "threads.net", "wikipedia.org",
               "wikimedia.org", "wikidata.org", "maxpreps.com", "yelp.com", "mapquest.com",
               "google.com", "bing.com", "yahoo.com", "tripadvisor.com", "indeed.com", "glassdoor.com",
               "zoominfo.com", "bizapedia.com", "opencorporates.com", "nextdoor.com", "eventbrite.com",
               "advancedeventsystems.com", "usavolleyball.org", "hudl.com", "volleyballlife.com",
               "ncsasports.org", "prepvolleyball.com", "manta.com", "dnb.com", "crunchbase.com",
               "gofundme.com", "meetup.com", "amazon.com", "ebay.com", "yp.com", "apple.com")


@dataclass
class Candidate:
    id: str
    url: str
    source: str  # "Wikipedia" / "Wikidata" / "Commons" / "Website" / "Web search" / "Image search"
    title: str
    page: Optional[str] = None
    fallback: Optional[str] = None  # e.g. a search engine's thumbnail


@dataclass
class FoundColor:
    hex: str
    source: str


_candidates: "OrderedDict[str, Candidate]" = OrderedDict()
_images: "OrderedDict[str, bytes]" = OrderedDict()
_cache_lock = threading.Lock()


def _remember(c: Candidate) -> None:
    with _cache_lock:
        _candidates[c.id] = c
        _candidates.move_to_end(c.id)
        while len(_candidates) > 500:
            _candidates.popitem(last=False)


def candidate(cid: str) -> Candidate:
    with _cache_lock:
        c = _candidates.get(cid)
    if c is None:
        raise LookupError("That result has expired - search again")
    return c


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


class Busy(Exception):
    """Wikimedia asked us to slow down."""


def _wm(s: requests.Session, api: str, params: dict) -> dict:
    """A Wikimedia API call; waits once when told to slow down briefly."""
    params = {"format": "json", "formatversion": 2, **params}
    for attempt in range(2):
        r = s.get(api, params=params, timeout=TIMEOUT)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "5") or 5)
            if attempt == 0 and wait <= 8:
                time.sleep(wait)
                continue
            raise Busy("busy - try again in a minute")
        r.raise_for_status()
        return r.json()
    raise Busy("busy - try again in a minute")


# ------------------------------------------------------------- fetching

def _public(url: str) -> bool:
    """http(s) to an address on the public internet - not this PC, not
    the home network."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except (OSError, ValueError):
        return False


def fetch(url: str, *, limit: int, session: Optional[requests.Session] = None) -> tuple[bytes, str, str]:
    """GET a public address, following up to 4 redirects that stay
    public. Returns (body, content type, final url)."""
    s = session or _session()
    for _ in range(5):
        if not _public(url):
            raise ValueError(f"Not a public address: {url}")
        r = s.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location", ""))
            r.close()
            continue
        r.raise_for_status()
        data = bytearray()
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > limit:
                r.close()
                raise ValueError("Too large")
        return bytes(data), r.headers.get("Content-Type", ""), url
    raise ValueError("Too many redirects")


def remember_upload(data: bytes) -> str:
    """An image uploaded or pasted into the logo editor, kept like a
    found one, so the same cleanup (GET /club-lookup/{id}/image) serves
    both. Raises ValueError for anything Pillow can't read."""
    try:
        logo_tools.load(data)
    except Exception as exc:
        raise ValueError("Not an image VME can read (PNG, JPEG, WebP, GIF or BMP)") from exc
    cid = "u" + hashlib.sha1(data).hexdigest()[:15]
    _remember(Candidate(id=cid, url="", source="Upload", title="Uploaded image"))
    with _cache_lock:
        _images[cid] = data
        _images.move_to_end(cid)
        while len(_images) > 40:
            _images.popitem(last=False)
    return cid


def image_bytes(cid: str) -> bytes:
    """The candidate's image, fetched once and kept for a while."""
    with _cache_lock:
        if cid in _images:
            _images.move_to_end(cid)
            return _images[cid]
    c = candidate(cid)
    last: Exception = ValueError("No image")
    for url in filter(None, [c.url, c.fallback]):
        try:
            data, ctype, _ = fetch(url, limit=MAX_IMAGE_BYTES)
            if "svg" in ctype or "html" in ctype:
                raise ValueError(f"Not an image VME can read ({ctype or 'unknown'})")
            logo_tools.load(data)  # readable?
            break
        except Exception as exc:  # try the fallback
            last = exc
    else:
        raise ValueError(f"Could not get that image: {last}")
    with _cache_lock:
        _images[cid] = data
        while len(_images) > 40:
            _images.popitem(last=False)
    return data


# --------------------------------------------------------------- search

def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.casefold()) if len(t) > 1 and t not in _STOP}


def _relevant(name: str, title: str) -> bool:
    """Every telling word of the name is in the title, whole, shortened
    ("Prep" - Preparatory) or spelled out ("NW" - Northwest)."""
    have = {t for t in re.findall(r"[a-z0-9]+", title.casefold())}
    for w in _tokens(name):
        full = _SHORT.get(w, w)
        if not any(h.startswith(w) or h.startswith(full) or (len(h) > 2 and w.startswith(h)) for h in have):
            return False
    return True


_URL = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", re.I)


def split_hint(hint: str) -> tuple[str, list[str]]:
    """"Seattle nwjuniors.org" -> ("Seattle", ["https://nwjuniors.org"])."""
    urls = []
    for m in _URL.findall(hint or ""):
        if "." in m.split("/")[0 if "://" not in m else 2]:
            urls.append(m if "://" in m else f"https://{m}")
    words = _URL.sub(" ", hint or "")
    return " ".join(words.split()), urls


def lookup(name: str, hint: str = "") -> dict[str, Any]:
    name = " ".join((name or "").split())
    words, sites = split_hint(hint)
    query = f"{name} {words}".strip()
    notes: list[str] = []
    found: list[Candidate] = []
    colors: list[FoundColor] = []
    key = os.environ.get("VME_BRAVE_API_KEY", "").strip()

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {
            "Wikipedia": pool.submit(_wikipedia, name, query),
            "Commons": pool.submit(_commons, name, query),
        }
        # Looking for the club's site is moot when it was typed in.
        if key and not sites:
            jobs["Web search"] = pool.submit(_web_search, query, key)
        if key:
            jobs["Image search"] = pool.submit(_brave_images, query, key)
        site_jobs = [(pool.submit(_website, u), True) for u in sites[:2]]
        results: dict[str, Any] = {}
        for label, job in jobs.items():
            try:
                results[label] = job.result()
            except Exception as exc:
                logger.warning("club lookup: %s failed: %s", label, exc)
                why = str(exc) if isinstance(exc, Busy) else exc.__class__.__name__
                notes.append(f"{label} didn't answer ({why}).")
        # A website from Wikidata when none was typed, and the club's
        # own sites among the web results.
        wiki_site = (results.get("Wikipedia") or {}).get("website")
        if not sites and wiki_site:
            site_jobs.append((pool.submit(_website, wiki_site), True))
        read = {(urlparse(u).hostname or "").removeprefix("www.") for u in [*sites, wiki_site] if u}
        for u in club_sites(name, results.get("Web search") or []):
            host = (urlparse(u).hostname or "").removeprefix("www.")
            if host not in read:
                read.add(host)
                site_jobs.append((pool.submit(_website, u, "Web search"), False))
        for job, typed in site_jobs:
            try:
                results.setdefault("sites", []).append(job.result())
            except Exception as exc:
                logger.info("club lookup: website failed: %s", exc)
                # A guessed site that won't open isn't news; a typed one is.
                if typed:
                    notes.append(f"The website couldn't be read ({exc}).")

    for part in [results.get("Wikipedia"), *results.get("sites", []), results.get("Commons"), results.get("Image search")]:
        if not part:
            continue
        found += part.get("candidates", [])
        colors += part.get("colors", [])

    if not key:
        notes.append("Web and image search are off: add a Brave Search API key as VME_BRAVE_API_KEY "
                     "to find the club's website and look across the whole web. Meanwhile a website "
                     "typed into the hints is read directly.")
    seen: set[str] = set()
    cands = []
    for c in found:
        if c.id in seen:
            continue
        seen.add(c.id)
        _remember(c)
        cands.append(c)
    hexes: set[str] = set()
    cols = []
    for fc in colors:
        if fc.hex not in hexes:
            hexes.add(fc.hex)
            cols.append(fc)
    return {
        "query": query,
        # url is for showing the source; images are fetched by id only.
        "candidates": [asdict(c) | {"fallback": None} for c in cands[:30]],
        "colors": [asdict(c) for c in cols[:12]],
        "notes": notes,
    }


def _cand(url: str, source: str, title: str, page: Optional[str] = None, fallback: Optional[str] = None) -> Candidate:
    return Candidate(id=hashlib.sha1(url.encode()).hexdigest()[:16], url=url, source=source,
                     title=title, page=page, fallback=fallback)


def _file_name(value: str) -> Optional[str]:
    """`[[File:Juanita_High_School_Crest.png|200px]]` -> the file name."""
    v = re.sub(r"^\s*\[\[|\]\].*$", "", value.strip())
    v = re.sub(r"^(File|Image)\s*:", "", v.strip(), flags=re.I).split("|")[0].strip()
    return v if v.lower().endswith(IMAGE_EXT) else None


def _imageinfo(api: str, files: list[str], s: requests.Session) -> dict[str, dict]:
    """File name -> {url (a PNG rendering at most 512 wide), page}."""
    out: dict[str, dict] = {}
    for i in range(0, len(files), 40):
        batch = files[i:i + 40]
        q = _wm(s, api, {
            "action": "query", "titles": "|".join(f"File:{f}" for f in batch), "prop": "imageinfo",
            "iiprop": "url", "iiurlwidth": 512,
        }).get("query", {})
        norm = {n["to"]: n["from"] for n in q.get("normalized", [])}
        for p in q.get("pages", []):
            info = (p.get("imageinfo") or [None])[0]
            if not info:
                continue
            title = p["title"]
            name = norm.get(title, title).split(":", 1)[-1]
            out[name.replace("_", " ")] = {
                "url": info.get("thumburl") or info.get("url"),
                "page": info.get("descriptionurl"),
            }
    return out


def _wikipedia(name: str, query: str) -> dict[str, Any]:
    s = _session()
    # The search, each article's infobox (section 0) and its images, at once.
    q = _wm(s, WIKI_API, {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 5,
        "prop": "pageprops|revisions|images", "ppprop": "wikibase_item",
        "rvprop": "content", "rvslots": "main", "rvsection": 0, "imlimit": "max",
    }).get("query", {})
    pages = sorted(q.get("pages", []), key=lambda p: p.get("index", 0))
    pages = [p for p in pages if _relevant(name, p["title"])][:2]
    files: list[tuple[str, str, str]] = []  # (file, source label, article)
    colors: list[FoundColor] = []
    qids = {}
    for p in pages:
        title = p["title"]
        if p.get("pageprops", {}).get("wikibase_item"):
            qids[p["pageprops"]["wikibase_item"]] = title
        rev = (p.get("revisions") or [{}])[0]
        text = rev.get("slots", {}).get("main", {}).get("content", "") or rev.get("content", "")
        # Within the line: an empty field mustn't swallow the next one.
        for field, value in re.findall(r"^[ \t]*\|[ \t]*([a-z_ ]+?)[ \t]*=[ \t]*(\S.*)$", text, flags=re.M | re.I):
            field = field.strip().casefold()
            if re.fullmatch(r"(school_|official_|athletics_)?colou?rs?", field):
                colors += [FoundColor(h, f"Wikipedia: {title}") for h in logo_tools.colors_from_wikitext(value)]
            elif _LOGOISH.search(field) or field in ("image", "image_name"):
                f = _file_name(value)
                if f and (_LOGOISH.search(field) or _LOGOISH.search(f)):
                    files.append((f, "Wikipedia", title))
        for img in p.get("images", []):
            f = img["title"].split(":", 1)[-1]
            if _LOGOISH.search(f) and f.lower().endswith(IMAGE_EXT):
                files.append((f, "Wikipedia", title))
    files = [x for x in files if not _JUNK.search(x[0])]

    website = None
    if qids:
        wd = _wikidata(list(qids), s)
        for qid, item in wd.items():
            title = qids.get(qid, qid)
            files += [(f, "Wikidata", title) for f in item["files"]]
            colors += [FoundColor(h, f"Wikidata: {title}") for h in item["colors"]]
            website = website or item["website"]

    names = list(dict.fromkeys(f.replace("_", " ") for f, _, _ in files))
    info = _imageinfo(WIKI_API, names, s) if names else {}
    cands = []
    for f, source, title in files:
        i = info.get(f.replace("_", " "))
        if i and i["url"]:
            cands.append(_cand(i["url"], source, f"{title} - {f}", i.get("page")))
    return {"candidates": cands, "colors": colors, "website": website}


def _claim_values(entity: dict, prop: str) -> list[Any]:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            out.append(v)
    return out


def _wikidata(qids: list[str], s: requests.Session) -> dict[str, dict]:
    entities = _wm(s, WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(qids),
                                     "props": "claims"}).get("entities", {})
    out: dict[str, dict] = {}
    color_ids: dict[str, list[str]] = {}
    for qid, e in entities.items():
        # P154 logo, P158 seal, P94 coat of arms
        files = [v for p in ("P154", "P158", "P94") for v in _claim_values(e, p) if isinstance(v, str)]
        sites = [v for v in _claim_values(e, "P856") if isinstance(v, str)]
        color_ids[qid] = [v["id"] for v in _claim_values(e, "P6364") if isinstance(v, dict) and "id" in v]
        out[qid] = {"files": files, "colors": [], "website": sites[0] if sites else None}
    all_colors = sorted({c for ids in color_ids.values() for c in ids})
    if all_colors:
        ents = _wm(s, WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(all_colors[:50]),
                                     "props": "claims|labels", "languages": "en"}).get("entities", {})
        if ents:
            for qid, ids in color_ids.items():
                for cid in ids:
                    e = ents.get(cid, {})
                    hexes = [v for v in _claim_values(e, "P465") if isinstance(v, str)]  # sRGB hex
                    label = e.get("labels", {}).get("en", {}).get("value", "")
                    h = logo_tools.color_hex(hexes[0]) if hexes else logo_tools.color_hex(label)
                    if h:
                        out[qid]["colors"].append(h)
    return out


def _commons(name: str, query: str) -> dict[str, Any]:
    s = _session()
    q = _wm(s, COMMONS_API, {
        "action": "query", "generator": "search", "gsrsearch": f"{query} logo", "gsrnamespace": 6,
        "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 512,
    }).get("query", {})
    cands = []
    for p in sorted(q.get("pages", []), key=lambda p: p.get("index", 0)):
        t = p["title"].split(":", 1)[-1]
        info = (p.get("imageinfo") or [None])[0]
        # Named after the club and as a badge - not any photo of its town.
        if info and _relevant(name, t) and _LOGOISH.search(t) and not _JUNK.search(t)                 and t.lower().endswith(IMAGE_EXT):
            cands.append(_cand(info.get("thumburl") or info["url"], "Commons", t, info.get("descriptionurl")))
    return {"candidates": cands[:6]}


_VOID = {"img", "link", "meta", "br", "hr", "input", "source", "area", "base", "col", "embed", "wbr", "track"}
_HEADERISH = re.compile(r"header|navbar|masthead|brand|site-title|topbar", re.I)
_LD_LOGO = re.compile(r'"logo"\s*:\s*(?:\{[^{}]*?"(?:url|contentUrl)"\s*:\s*)?"([^"]+)"')


class _Page(HTMLParser):
    """Logo images, icons, share images and the theme colour.

    An <img> counts as the logo when it, or something it sits in, is
    called "logo" (class, id, alt or file name) - the usual way a site
    marks the spot - or when the page's structured data names it. The
    first images in the header come next: often the logo under a plain
    name.
    """

    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[int, str, str]] = []  # (rank, url, what)
        self.theme: list[str] = []
        self.title = ""
        self._in_title = False
        self._in_ld = False
        self._ld = ""
        # Open elements: (tag, in a logo spot, in the header).
        self._open: list[tuple[str, bool, bool]] = []
        self._header_imgs = 0

    def _inside(self) -> tuple[bool, bool]:
        return any(o[1] for o in self._open), any(o[2] for o in self._open)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        named = " ".join([a.get("class", ""), a.get("id", "")])
        if tag not in _VOID:
            logo_in, head_in = self._inside()
            self._open.append((tag, logo_in or bool(_LOGOISH.search(named)),
                               head_in or tag in ("header", "nav") or bool(_HEADERISH.search(named))))
        if tag == "title":
            self._in_title = True
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_ld = True
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "apple-touch-icon" in rel:
                self.images.append((1, a.get("href", ""), "App icon"))
            elif "icon" in rel and "mask" not in rel:
                size = max([int(x) for x in re.findall(r"(\d+)x\d+", a.get("sizes", ""))] or [0])
                self.images.append((2 if size >= 96 else 5, a.get("href", ""), "Site icon"))
        elif tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:image", "twitter:image"):
                self.images.append((4, a.get("content", ""), "Share image"))
            elif prop in ("theme-color", "msapplication-tilecolor"):
                self.theme.append(a.get("content", ""))
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or a.get("data-lazy-src") or ""
            if not src and a.get("srcset"):
                src = a["srcset"].split(",")[0].split()[0]
            what = " ".join([src, a.get("alt", ""), named])
            logo_in, head_in = self._inside()
            if logo_in or _LOGOISH.search(what):
                self.images.append((0, src, a.get("alt") or "Logo"))
            elif head_in and self._header_imgs < 2:
                self._header_imgs += 1
                self.images.append((3, src, a.get("alt") or "Header image"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            for m in _LD_LOGO.findall(self._ld):
                self.images.append((0, m.replace("\\/", "/"), "Logo (site data)"))
            self._ld = ""
        # Close back to the matching element; a stray end tag is ignored.
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                del self._open[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_ld:
            self._ld += data


def _website(url: str, source: str = "Website") -> dict[str, Any]:
    body, ctype, final = fetch(url, limit=MAX_PAGE_BYTES)
    if "html" not in ctype:
        raise ValueError("not a web page")
    page = _Page()
    page.feed(body.decode("utf-8", errors="replace"))
    host = urlparse(final).hostname or final
    cands = []
    seen: set[str] = set()
    for rank, src, what in sorted(page.images, key=lambda x: x[0]):
        if not src or src.startswith("data:"):
            continue
        full = urljoin(final, src.strip())
        if urlparse(full).path.lower().endswith(".svg") or full in seen:
            continue
        seen.add(full)
        cands.append(_cand(full, source, f"{host} - {what}", final))
    # The favicon at the usual place, when the page names none.
    if not any(c.title.endswith("icon") for c in cands):
        cands.append(_cand(urljoin(final, "/favicon.ico"), source, f"{host} - Site icon", final))
    colors = [FoundColor(h, f"Website: {host}") for t in page.theme if (h := logo_tools.color_hex(t))]
    return {"candidates": cands[:8], "colors": colors}


def _brave_images(query: str, key: str) -> dict[str, Any]:
    r = requests.get(BRAVE_IMAGES, params={"q": f"{query} logo", "count": 20, "safesearch": "strict"},
                     headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": UA},
                     timeout=TIMEOUT)
    r.raise_for_status()
    cands = []
    for res in r.json().get("results", []):
        full = (res.get("properties") or {}).get("url")
        thumb = (res.get("thumbnail") or {}).get("src")
        if not (full or thumb):
            continue
        cands.append(_cand(full or thumb, "Image search", res.get("title") or res.get("source") or "",
                           res.get("url"), fallback=thumb if full else None))
    return {"candidates": cands[:16]}


def _web_search(query: str, key: str) -> list[tuple[str, str]]:
    """Step one: ordinary web results, as (url, title)."""
    r = requests.get(BRAVE_WEB, params={"q": query, "count": 10, "safesearch": "strict"},
                     headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": UA},
                     timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for res in (r.json().get("web") or {}).get("results", []):
        url = res.get("url") or ""
        if url.startswith(("http://", "https://")):
            out.append((url, re.sub(r"<[^>]+>", "", res.get("title") or "")))
    return out


def _host_named(name: str, host: str) -> bool:
    """The site's address is made of the club's name: nwjuniors.org for
    Northwest Juniors, seattlevb.sportngin.com for Seattle VB."""
    label = "".join(re.findall(r"[a-z0-9]+", ".".join(host.split(".")[:-1])))
    words = re.findall(r"[a-z0-9]+", name.casefold())
    # Initials: spvb for Seattle Pacific VB.
    initials = "".join(w[0] for w in words)
    if len(initials) >= 3 and initials in label:
        return True
    hits, telling = 0, False
    for w in words:
        forms = {w, _SHORT.get(w, w), _LONG.get(w, w)}
        if any(len(f) >= 2 and f in label for f in forms):
            hits += 1
            telling = telling or (w not in _STOP and len(w) >= 4 and w in label)
    return telling or hits >= 2


def club_sites(name: str, results: list[tuple[str, str]], limit: int = 3) -> list[str]:
    """Step two's pick: the results that look like the club's own site -
    named like the club in the address; failing that, in the title (a
    news story about the club is named so too, hence second); never a
    directory, social network or search engine. When nothing is named,
    the first plain result, as a guess."""
    ranked = []
    hosts: set[str] = set()
    for i, (url, title) in enumerate(results):
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or host in hosts or any(host == d or host.endswith("." + d) for d in _NOT_A_CLUB):
            continue
        hosts.add(host)
        rank = 0 if _host_named(name, host) else 1 if _relevant(name, title) else 2
        ranked.append((rank, i, url))
    ranked.sort()
    best = ranked[0][0] if ranked else 2
    if best == 2:
        return [u for _, _, u in ranked[:1]]
    return [u for r, _, u in ranked if r == best][:limit if best == 0 else 2]
