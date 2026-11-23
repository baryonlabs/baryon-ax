"""Web pages as knowledge: a link in a question is read, kept as a document and used as evidence.

"https://www.example.com/ 이 사이트 이해해줘" → the page (and, if asked, a few pages it links to on the
same site) is fetched, turned into Markdown and indexed like an upload, so the answer cites it and
later questions find it too.

Safety:
* only http and https; every hop of a redirect is checked;
* the host must resolve to public addresses: loopback, private, link-local, multicast and reserved
  ranges are refused (no reaching the company network through the server), unless
  WEB_FETCH_ALLOW_PRIVATE=true for an intranet deployment that wants exactly that;
* at most WEB_FETCH_MAX_BYTES per page, text/html, application/xhtml+xml or text/plain only;
* extra pages of a site follow its robots.txt; the page the person gave is read as their request.

The HTML is reduced with the standard library: headings, paragraphs, lists, table rows and link
text are kept; scripts, styles, navigation, headers, footers and forms are dropped.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

USER_AGENT = "BaryonAX-WebReader/0.7 (+https://baryon-ax.baryon.ai)"
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]}>,|]+", re.I)  # "|": Slack sends links as <url|label>
SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template", "button", "select"}
BLOCK = {"p", "div", "section", "article", "main", "br", "tr", "blockquote", "pre", "figure", "figcaption", "dd", "dt"}


def enabled() -> bool:
    return os.getenv("WEB_FETCH_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def allow_private() -> bool:
    return os.getenv("WEB_FETCH_ALLOW_PRIVATE", "false").strip().lower() in {"1", "true", "yes", "on"}


def max_bytes() -> int:
    return int(os.getenv("WEB_FETCH_MAX_BYTES", str(3_000_000)))


def extract_urls(text: str, limit: int = 3) -> list[str]:
    """Links in a message, without trailing punctuation, at most ``limit``, in order."""
    urls: list[str] = []
    for match in URL_PATTERN.findall(text or ""):
        url = match.rstrip(".,;:!?…'\"")
        if url not in urls:
            urls.append(url)
    return urls[:limit]


class WebError(Exception):
    """Why a page could not be read, in words for the person who asked."""


def check_host(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebError("http 또는 https 주소만 읽을 수 있어요.")
    if allow_private():
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise WebError(f"주소를 찾을 수 없어요: {parsed.hostname}") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global or address.is_multicast:
            raise WebError("사내망이나 내부 주소는 읽지 않아요.")


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    links: list[str] = field(default_factory=list)


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.skip = 0
        self.title = ""
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.prefix = ""
        self.cells: list[str] | None = None

    def flush(self) -> None:
        text = " ".join("".join(self.current).split())
        if text:
            self.lines.append(self.prefix + text)
        self.current, self.prefix = [], ""

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag in SKIP:
            self.skip += 1
            return
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key in {"description", "og:description", "og:title", "og:site_name"}:
                self.meta[key] = " ".join(attributes.get("content", "").split())
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.prefix = "#" * int(tag[1]) + " "
        elif tag == "li":
            self.flush()
            self.prefix = "- "
        elif tag in {"td", "th"} and self.cells is not None:
            self.flush_cell_start()
        elif tag == "tr":
            self.flush()
            self.cells = []
        elif tag in BLOCK:
            self.flush()

    def flush_cell_start(self) -> None:
        text = " ".join("".join(self.current).split())
        if text and self.cells is not None:
            self.cells.append(text)
        self.current = []

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
        if self.skip:
            return
        if tag in {"td", "th"} and self.cells is not None:
            self.flush_cell_start()
        elif tag == "tr" and self.cells is not None:
            self.flush_cell_start()
            if self.cells:
                self.lines.append("| " + " | ".join(self.cells) + " |")
            self.cells = None
        elif re.fullmatch(r"h[1-6]", tag) or tag in BLOCK or tag == "li":
            self.flush()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.current.append(data)


def html_to_markdown(html: str, url: str = "") -> Page:
    reader = _Reader()
    reader.feed(html)
    reader.flush()
    title = " ".join((reader.meta.get("og:title") or reader.title).split()) or url
    lines: list[str] = []
    for line in reader.lines:
        if not lines or lines[-1] != line:  # menus repeated in several places
            lines.append(line)
    description = reader.meta.get("og:description") or reader.meta.get("description") or ""
    body = "\n\n".join(lines)
    if description and description not in body:
        body = f"{description}\n\n{body}"
    links = []
    for href in reader.links:
        absolute = urllib.parse.urljoin(url, href).split("#")[0]
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
    return Page(url=url, title=title, markdown=body.strip(), links=links)


async def fetch(url: str, client: httpx.AsyncClient | None = None) -> Page:
    """One page, following up to five redirects, each one checked."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT,
                                                             "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"})
    try:
        current = url
        for _ in range(6):
            check_host(current)
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                    current = urllib.parse.urljoin(current, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise WebError(f"페이지가 {response.status_code} 오류를 돌려줬어요.")
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind not in {"text/html", "application/xhtml+xml", "text/plain", ""}:
                    raise WebError(f"웹 페이지가 아니에요({kind}). 파일은 문서 올리기로 올려 주세요.")
                data = bytearray()
                async for piece in response.aiter_bytes():
                    data += piece
                    if len(data) > max_bytes():
                        raise WebError("페이지가 너무 커요.")
                text = bytes(data).decode(response.encoding or "utf-8", errors="replace")
                if kind == "text/plain":
                    return Page(url=current, title=current, markdown=text.strip())
                return html_to_markdown(text, current)
        raise WebError("리디렉션이 너무 많아요.")
    except httpx.HTTPError as error:
        raise WebError(f"페이지를 가져오지 못했어요: {type(error).__name__}") from error
    finally:
        if own:
            await client.aclose()


async def read_site(url: str, pages: int = 1, client: httpx.AsyncClient | None = None) -> list[Page]:
    """The page, then up to ``pages - 1`` more pages it links to on the same site (robots.txt permitting)."""
    first = await fetch(url, client)
    found = [first]
    if pages <= 1:
        return found
    origin = urllib.parse.urlsplit(first.url)
    robots = urllib.robotparser.RobotFileParser()
    try:
        robots_page = await fetch(f"{origin.scheme}://{origin.netloc}/robots.txt", client)
        robots.parse(robots_page.markdown.splitlines())
    except WebError:
        robots.parse([])
    seen = {first.url}
    for link in first.links:
        if len(found) >= pages:
            break
        parsed = urllib.parse.urlsplit(link)
        if parsed.netloc != origin.netloc or link in seen or re.search(r"\.(pdf|jpe?g|png|gif|zip|mp4|svg)$", parsed.path, re.I):
            continue
        seen.add(link)
        if not robots.can_fetch(USER_AGENT, link):
            continue
        try:
            found.append(await fetch(link, client))
        except WebError:
            continue
    return found


def page_document(page: Page) -> tuple[str, bytes]:
    """The filename and Markdown body a page is stored as."""
    host = urllib.parse.urlsplit(page.url).netloc
    safe_title = " ".join(re.sub(r"[\\/:*?\"<>|\n\r]+", " ", page.title).split())[:80] or host
    body = f"# {page.title}\n\n출처: {page.url}\n\n{page.markdown}\n"
    return f"웹 · {safe_title} ({host}).md", body.encode("utf-8")


# --- crawling a whole site -------------------------------------------------------------------

TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|ref|mc_cid|mc_eid)$", re.I)
SKIP_PATH = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|ico|zip|gz|mp3|mp4|mov|avi|css|js|json|xml|rss|woff2?|ttf|exe|dmg)$", re.I)
CRAWL_INTENT = re.compile(r"크롤링|크롤|사이트\s*전체|전체\s*(?:페이지|사이트)|모든\s*(?:페이지|쪽)|싹\s*다|다\s*읽어|crawl", re.I)


def crawl_limit() -> int:
    return int(os.getenv("WEB_CRAWL_MAX_PAGES", "200"))


def normalize_url(url: str) -> str:
    """One spelling per page: no fragment, no tracking parameters, lowercase host, no trailing slash but the root."""
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode([(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                                    if not TRACKING.match(key)])
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def wants_crawl(text: str) -> bool:
    """"이 사이트 크롤링해 줘", "전체 페이지 다 읽어": a whole site, not just the linked page."""
    return bool(CRAWL_INTENT.search(URL_PATTERN.sub(" ", text or "")))


def sitemap_urls(xml: str) -> list[str]:
    return [" ".join(match.split()) for match in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml or "", re.I)]


async def crawl(url: str, max_pages: int = 30, path_prefix: str = "", client: httpx.AsyncClient | None = None,
                on_page=None, should_stop=None, delay: float | None = None) -> list[Page]:
    """Breadth-first over one site: the start page, its sitemap, then links found on the pages read.

    Stays on the start page's host and under ``path_prefix`` (default: the whole site), obeys robots.txt
    and its Crawl-delay (at least ``delay`` seconds between requests, WEB_CRAWL_DELAY, default 0.5),
    skips files that are not pages, and calls ``on_page(page)`` after each page so a job can store it
    and report progress. ``should_stop()`` ends the crawl early (a cancelled job)."""
    import asyncio

    own = client is None
    client = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT,
                                                             "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"})
    max_pages = max(1, min(max_pages, crawl_limit()))
    try:
        start = normalize_url(url)
        first = await fetch(start, client)
        origin = urllib.parse.urlsplit(first.url)
        prefix = path_prefix or "/"
        robots = urllib.robotparser.RobotFileParser()
        sitemaps: list[str] = [f"{origin.scheme}://{origin.netloc}/sitemap.xml"]
        try:
            robots_text = (await fetch(f"{origin.scheme}://{origin.netloc}/robots.txt", client)).markdown
            robots.parse(robots_text.splitlines())
            sitemaps = [line.split(":", 1)[1].strip() for line in robots_text.splitlines()
                        if line.lower().startswith("sitemap:")] or sitemaps
        except WebError:
            robots.parse([])
        pause = max(delay if delay is not None else float(os.getenv("WEB_CRAWL_DELAY", "0.5")),
                    float(robots.crawl_delay(USER_AGENT) or 0))
        pause = min(pause, 5.0)

        def wanted(link: str) -> bool:
            parts = urllib.parse.urlsplit(link)
            return (parts.scheme in {"http", "https"} and parts.netloc.lower() == origin.netloc.lower()
                    and parts.path.startswith(prefix) and not SKIP_PATH.search(parts.path)
                    and robots.can_fetch(USER_AGENT, link))

        queue: list[str] = []
        seen = {normalize_url(first.url), start}
        for sitemap in sitemaps[:3]:
            try:
                for link in sitemap_urls((await _raw(sitemap, client)))[:max_pages * 3]:
                    link = normalize_url(link)
                    if link not in seen and wanted(link):
                        seen.add(link)
                        queue.append(link)
            except WebError:
                continue
        pages = [first]
        digests = {hash(first.markdown)}  # "/" and "/index.html" are often the same page
        if on_page:
            await on_page(first)
        frontier = list(first.links)
        while len(pages) < max_pages and not (should_stop and should_stop()):
            for link in frontier:
                link = normalize_url(link)
                if link not in seen and wanted(link):
                    seen.add(link)
                    queue.append(link)
            frontier = []
            if not queue:
                break
            link = queue.pop(0)
            await asyncio.sleep(pause)
            try:
                page = await fetch(link, client)
            except WebError:
                continue
            if not page.markdown.strip() or hash(page.markdown) in digests:
                continue
            digests.add(hash(page.markdown))
            pages.append(page)
            frontier = page.links
            if on_page:
                await on_page(page)
        return pages
    finally:
        if own:
            await client.aclose()


async def _raw(url: str, client: httpx.AsyncClient) -> str:
    """A sitemap's XML as text (fetch() would turn XML into page text)."""
    check_host(url)
    response = await client.get(url, follow_redirects=False)
    if response.status_code >= 400:
        raise WebError(f"{response.status_code}")
    return response.text[:5_000_000]
