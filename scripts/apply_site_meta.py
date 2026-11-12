#!/usr/bin/env python3
"""Sync social metadata and structured data for the static site.

    python3 scripts/apply_site_meta.py en
    python3 scripts/apply_site_meta.py ko
    python3 scripts/apply_site_meta.py all

Reads the visible title/description and FAQ from each page, then rewrites:
- Open Graph and Twitter tags (title, description, image, image alt, type)
- JSON-LD: WebSite, WebPage, SoftwareApplication, SoftwareSourceCode,
  HowTo (quick start), BreadcrumbList, FAQPage (from visible Q&A)
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BASE = "https://baryonlabs.github.io/baryon-ax/"

PAGES = {
    "ko": {
        "path": SITE / "index.html",
        "url": BASE,
        "lang": "ko",
        "image": BASE + "assets/og.jpg",
        "image_alt": "Baryon AX — 근거로 답하는 온프레미스 AI 에이전트 플랫폼",
        "howto_name": "Docker로 Baryon AX 실행하기",
        "howto_steps": [
            ("저장소 복제", "git clone https://github.com/baryonlabs/baryon-ax.git"),
            ("환경 변수 준비", "cp .env.example .env"),
            ("스택 실행", "docker compose up --build"),
            ("접속", "http://localhost:3001 을 연다"),
        ],
    },
    "en": {
        "path": SITE / "en" / "index.html",
        "url": BASE + "en/",
        "lang": "en",
        "image": BASE + "assets/og-en.jpg",
        "image_alt": "Baryon AX — on-premises AI agents with grounded RAG and citations",
        "howto_name": "Run Baryon AX with Docker",
        "howto_steps": [
            ("Clone the repository", "git clone https://github.com/baryonlabs/baryon-ax.git"),
            ("Prepare environment variables", "cp .env.example .env"),
            ("Start the stack", "docker compose up --build"),
            ("Open the UI", "http://localhost:3001"),
        ],
    },
}


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def set_meta(markup: str, attr: str, key: str, content: str) -> str:
    pattern = rf'<meta {attr}="{re.escape(key)}" content="[^"]*">'
    replacement = f'<meta {attr}="{key}" content="{html.escape(content, quote=True)}">'
    if re.search(pattern, markup):
        return re.sub(pattern, replacement, markup, count=1)
    return markup.replace("</head>", f"{replacement}\n</head>", 1)


def ensure_meta(markup: str, attr: str, key: str, content: str) -> str:
    return set_meta(markup, attr, key, content)


def upgrade_jsonld(markup: str, config: dict, title: str, description: str, faq: list[tuple[str, str]]) -> str:
    match = re.search(r'(<script type="application/ld\+json">)(.*?)(</script>)', markup, re.S)
    if not match:
        return markup
    data = json.loads(match.group(2))
    graph = data.setdefault("@graph", [])
    by_type = {node.get("@type"): node for node in graph if isinstance(node, dict)}
    base_id = BASE

    org = by_type.get("Organization")
    if org is not None:
        org.setdefault("sameAs", "https://github.com/baryonlabs")

    app = by_type.get("SoftwareApplication")
    if app is not None:
        app["url"] = BASE
        app["image"] = config["image"]
        app["screenshot"] = config["image"]
        app["sameAs"] = "https://github.com/baryonlabs/baryon-ax"
        app["softwareHelp"] = config["url"]

    page = by_type.get("WebPage")
    if page is not None:
        page["url"] = config["url"]
        page["name"] = title
        page["description"] = description
        page["inLanguage"] = config["lang"]
        page["primaryImageOfPage"] = config["image"]
        page["isPartOf"] = {"@id": base_id + "#website"}

    if "WebSite" not in by_type:
        graph.append({
            "@type": "WebSite",
            "@id": base_id + "#website",
            "url": base_id,
            "name": "Baryon AX",
            "description": "On-premises AI agent and grounded RAG platform with citations, tacit knowledge, and governance.",
            "inLanguage": ["ko", "en"],
            "publisher": {"@id": base_id + "#org"},
        })

    if "SoftwareSourceCode" not in by_type:
        graph.append({
            "@type": "SoftwareSourceCode",
            "@id": base_id + "#source",
            "name": "Baryon AX",
            "description": "MIT-licensed on-premises AI agent and grounded RAG platform.",
            "codeRepository": "https://github.com/baryonlabs/baryon-ax",
            "license": "https://opensource.org/licenses/MIT",
            "programmingLanguage": ["Python", "TypeScript"],
            "runtimePlatform": "Docker, Kubernetes",
        })

    if "HowTo" not in by_type:
        graph.append({
            "@type": "HowTo",
            "@id": base_id + "#quickstart",
            "name": config["howto_name"],
            "totalTime": "PT10M",
            "tool": [{"@type": "HowToTool", "name": "Docker Compose"}],
            "step": [
                {"@type": "HowToStep", "position": index + 1, "name": name, "text": text}
                for index, (name, text) in enumerate(config["howto_steps"])
            ],
        })

    if "BreadcrumbList" not in by_type:
        items = [{"@type": "ListItem", "position": 1, "name": "Baryon AX", "item": BASE}]
        if config["lang"] != "ko":
            items.append({"@type": "ListItem", "position": 2, "name": "English", "item": config["url"]})
        graph.append({"@type": "BreadcrumbList", "@id": base_id + "#breadcrumb", "itemListElement": items})

    faq_node = by_type.get("FAQPage")
    if faq_node is None:
        faq_node = {"@type": "FAQPage", "@id": base_id + "#faq", "mainEntity": []}
        graph.append(faq_node)
    faq_node["mainEntity"] = [
        {"@type": "Question", "name": question, "acceptedAnswer": {"@type": "Answer", "text": answer}}
        for question, answer in faq[:8]
    ]

    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    return markup[: match.start(2)] + rendered + markup[match.end(2):]


def process(key: str) -> None:
    config = PAGES[key]
    path: Path = config["path"]
    markup = path.read_text(encoding="utf-8")

    title_match = re.search(r"<title>(.*?)</title>", markup, re.S)
    title = strip_tags(title_match.group(1)) if title_match else ""
    description_match = re.search(r'<meta name="description" content="([^"]*)"', markup)
    description = html.unescape(description_match.group(1)) if description_match else ""
    if not title or not description:
        raise SystemExit(f"{path}: title or description missing")

    faq = [
        (strip_tags(question), strip_tags(answer))
        for question, answer in re.findall(r"<details[^>]*>\s*<summary>(.*?)</summary>(.*?)</details>", markup, re.S)
    ]

    markup = ensure_meta(markup, "property", "og:type", "website")
    markup = ensure_meta(markup, "property", "og:site_name", "Baryon AX")
    markup = ensure_meta(markup, "property", "og:locale", "ko_KR" if key == "ko" else "en_US")
    markup = ensure_meta(markup, "property", "og:title", title)
    markup = ensure_meta(markup, "property", "og:description", description)
    markup = ensure_meta(markup, "property", "og:url", config["url"])
    markup = ensure_meta(markup, "property", "og:image", config["image"])
    markup = ensure_meta(markup, "property", "og:image:secure_url", config["image"])
    markup = ensure_meta(markup, "property", "og:image:type", "image/jpeg")
    markup = ensure_meta(markup, "property", "og:image:width", "1200")
    markup = ensure_meta(markup, "property", "og:image:height", "630")
    markup = ensure_meta(markup, "property", "og:image:alt", config["image_alt"])
    markup = ensure_meta(markup, "name", "twitter:card", "summary_large_image")
    markup = ensure_meta(markup, "name", "twitter:title", title)
    markup = ensure_meta(markup, "name", "twitter:description", description)
    markup = ensure_meta(markup, "name", "twitter:image", config["image"])
    markup = ensure_meta(markup, "name", "twitter:image:alt", config["image_alt"])
    markup = ensure_meta(markup, "name", "application-name", "Baryon AX")
    markup = ensure_meta(markup, "name", "apple-mobile-web-app-title", "Baryon AX")
    markup = ensure_meta(markup, "name", "referrer", "strict-origin-when-cross-origin")
    markup = ensure_meta(markup, "name", "format-detection", "telephone=no")

    if 'rel="alternate" type="text/plain"' not in markup:
        links = (
            f'<link rel="alternate" type="text/plain" href="{BASE}llms.txt" title="LLM brief">\n'
            f'<link rel="alternate" type="text/plain" href="{BASE}llms-full.txt" title="Extended LLM brief">\n'
        )
        markup = markup.replace("</head>", links + "</head>", 1)

    markup = upgrade_jsonld(markup, config, title, description, faq)
    path.write_text(markup, encoding="utf-8")
    print(f"{path}: title={len(title)}c, description={len(description)}c, faq={len(faq)}")


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in {"ko", "en", "all"}:
        print("usage: apply_site_meta.py ko|en|all", file=sys.stderr)
        return 2
    for key in ("ko", "en") if target == "all" else (target,):
        process(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
