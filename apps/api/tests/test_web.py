"""Web pages as knowledge (baryon/web.py and the chat path that reads links)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from baryon import web
from baryon.main import app

client = TestClient(app)
PUBLIC = "http://93.184.216.34"  # a public address literal: no DNS needed in tests

HTML = """<!doctype html><html><head><title>공공 AI | 홈</title>
<meta name="description" content="공공기관을 위한 AI 도입 플랫폼">
<script>var tracking = 1;</script><style>.x{}</style></head>
<body><nav><a href="/menu">메뉴</a></nav><header>로고</header>
<main><h1>공공 AI</h1><p>행정 문서를 <b>요약</b>하고 민원에 답합니다.</p>
<h2>주요 기능</h2><ul><li>문서 요약</li><li>민원 답변</li></ul>
<table><tr><th>요금제</th><th>가격</th></tr><tr><td>기본</td><td>월 10만원</td></tr></table>
<a href="/about">회사 소개</a><a href="https://other.example/x">외부</a></main>
<footer>저작권</footer></body></html>"""


def headers(user: str = "web-user", organization: str = "org-web", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def test_links_in_a_message() -> None:
    text = "https://www.gonggong.ai/ 이 사이트 이해해줘. 그리고 (https://a.example/b?c=1), https://www.gonggong.ai/"
    assert web.extract_urls(text) == ["https://www.gonggong.ai/", "https://a.example/b?c=1"]
    assert web.extract_urls("링크 없음") == []


def test_html_becomes_markdown_without_scripts_or_menus() -> None:
    page = web.html_to_markdown(HTML, "https://www.gonggong.ai/")
    assert page.title == "공공 AI | 홈"
    assert web.page_document(page)[0] == "웹 · 공공 AI 홈 (www.gonggong.ai).md"  # one space, safe for a filename
    assert page.markdown.startswith("공공기관을 위한 AI 도입 플랫폼")  # the description leads
    assert "# 공공 AI" in page.markdown and "## 주요 기능" in page.markdown
    assert "- 문서 요약" in page.markdown and "| 기본 | 월 10만원 |" in page.markdown
    assert "행정 문서를 요약하고 민원에 답합니다." in page.markdown
    for dropped in ("tracking", "메뉴", "로고", "저작권"):
        assert dropped not in page.markdown
    assert "https://www.gonggong.ai/about" in page.links and "https://other.example/x" in page.links


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/v1/admin", "http://10.0.0.5/", "http://169.254.169.254/latest",
                                 "http://[::1]/", "file:///etc/passwd", "ftp://example.com/"])
def test_internal_addresses_are_refused(url: str) -> None:
    with pytest.raises(web.WebError):
        web.check_host(url)


def test_internal_addresses_allowed_only_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("WEB_FETCH_ALLOW_PRIVATE", "true")
    web.check_host("http://10.0.0.5/")


def mock_client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_checks_every_redirect_and_the_content_type() -> None:
    async def run() -> None:
        ok = mock_client({f"{PUBLIC}/": httpx.Response(200, html=HTML)})
        page = await web.fetch(f"{PUBLIC}/", ok)
        assert page.title == "공공 AI | 홈"
        sneaky = mock_client({f"{PUBLIC}/go": httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})})
        with pytest.raises(web.WebError, match="내부"):
            await web.fetch(f"{PUBLIC}/go", sneaky)
        pdf = mock_client({f"{PUBLIC}/a.pdf": httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})})
        with pytest.raises(web.WebError, match="웹 페이지가 아니"):
            await web.fetch(f"{PUBLIC}/a.pdf", pdf)
        missing = mock_client({})
        with pytest.raises(web.WebError, match="404"):
            await web.fetch(f"{PUBLIC}/nope", missing)
    asyncio.run(run())


def test_a_site_follows_same_site_links_and_robots() -> None:
    about = "<html><head><title>회사 소개</title></head><body><p>2024년에 세운 회사입니다.</p></body></html>"
    secret = "<html><body><p>비공개</p></body></html>"
    home = HTML.replace('<a href="/about">회사 소개</a>', '<a href="/about">회사 소개</a><a href="/private/x">비공개</a>')

    async def run() -> list[web.Page]:
        site = mock_client({
            f"{PUBLIC}/": httpx.Response(200, html=home),
            f"{PUBLIC}/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /private/", headers={"content-type": "text/plain"}),
            f"{PUBLIC}/about": httpx.Response(200, html=about),
            f"{PUBLIC}/private/x": httpx.Response(200, html=secret),
        })
        return await web.read_site(f"{PUBLIC}/", pages=5, client=site)
    pages = asyncio.run(run())
    assert [page.url for page in pages] == [f"{PUBLIC}/", f"{PUBLIC}/about"]  # /menu is in <nav>, /private is disallowed


def test_a_link_in_a_question_is_read_once_and_used_as_evidence(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch(url: str, client=None) -> web.Page:
        calls.append(url)
        return web.html_to_markdown(HTML, url)

    monkeypatch.setattr(web, "fetch", fake_fetch)
    question = "https://www.gonggong.ai/ 이 사이트 이해해줘. 요금제는?"
    first = client.post("/v1/chat/completions", headers=headers(),
                        json={"messages": [{"role": "user", "content": question}], "stream": False}).json()
    titles = [item["title"] for item in first["citations"]]
    assert titles and all(title.startswith("웹 · 공공 AI") for title in titles[:2])
    assert any(step["step"] == "web_fetch" and step["status"] == "ok" for step in first["trace"]["steps"])
    again = client.post("/v1/chat/completions", headers=headers(),
                        json={"messages": [{"role": "user", "content": "https://www.gonggong.ai/ 주요 기능은?"}], "stream": False}).json()
    assert calls == ["https://www.gonggong.ai/"]  # fresh copy reused, not fetched again
    assert again["citations"][0]["title"].startswith("웹 · 공공 AI")
    listed = client.get("/v1/documents", headers=headers()).json()["data"]
    web_documents = [item for item in listed if item["title"].startswith("웹 · 공공 AI")]
    assert len(web_documents) == 1 and web_documents[0]["category"] == "web" and "site:www.gonggong.ai" in web_documents[0]["tags"]


def test_a_link_that_cannot_be_read_is_said_not_hidden(monkeypatch) -> None:
    async def refuse(url: str, client=None) -> web.Page:
        raise web.WebError("사내망이나 내부 주소는 읽지 않아요.")

    monkeypatch.setattr(web, "fetch", refuse)
    body = client.post("/v1/chat/completions", headers=headers(organization="org-web-fail"),
                       json={"messages": [{"role": "user", "content": "http://10.0.0.5/ 요약해 줘"}], "stream": False}).json()
    step = next(step for step in body["trace"]["steps"] if step["step"] == "web_fetch")
    assert step["status"] == "failed" and "내부 주소" in step["detail"]


def test_add_a_site_through_the_api(monkeypatch) -> None:
    async def fake_site(url: str, pages: int = 1, client=None) -> list[web.Page]:
        return [web.html_to_markdown(HTML, url), web.Page(url=url + "about", title="회사 소개", markdown="2024년에 세운 회사입니다.")][:pages]

    monkeypatch.setattr(web, "read_site", fake_site)
    response = client.post("/v1/documents/url", headers=headers(organization="org-web-api", roles="admin"),
                           json={"url": "https://www.gonggong.ai/", "pages": 2, "tags": "고객사"})
    assert response.status_code == 200
    stored = response.json()["data"]
    assert [item["url"] for item in stored] == ["https://www.gonggong.ai/", "https://www.gonggong.ai/about"]
    monkeypatch.setenv("WEB_FETCH_ENABLED", "false")
    assert client.post("/v1/documents/url", headers=headers(), json={"url": "https://www.gonggong.ai/"}).status_code == 403
