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
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]}>,]+", re.I)
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
