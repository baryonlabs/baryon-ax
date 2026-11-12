#!/usr/bin/env python3
"""Generate the static open-source/license pages from the app inventory.

    python3 scripts/build_open_source_site.py

The source of truth is `apps/api/src/baryon/open_source.py`, the same inventory
served by `GET /v1/open-source` and shown in Settings -> Open Source. Update
that file, then rerun this script and commit the generated pages.
"""

from __future__ import annotations

import collections
import html
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
INVENTORY = ROOT / "apps/api/src/baryon/open_source.py"

GROUP_ORDER = (
    "frontend",
    "backend",
    "data",
    "orchestration",
    "observability",
    "model",
    "ingestion",
    "infra",
    "dev",
)

GROUP_LABELS = {
    "ko": {
        "frontend": "프론트엔드",
        "backend": "백엔드",
        "data": "데이터·검색",
        "orchestration": "오케스트레이션",
        "observability": "관측·메모리",
        "model": "모델 런타임",
        "ingestion": "수집·파싱",
        "infra": "인프라",
        "dev": "개발·테스트",
    },
    "en": {
        "frontend": "Frontend",
        "backend": "Backend",
        "data": "Data & retrieval",
        "orchestration": "Orchestration",
        "observability": "Observability & memory",
        "model": "Model runtime",
        "ingestion": "Ingestion & parsing",
        "infra": "Infrastructure",
        "dev": "Development & test",
    },
}

STATUS_LABELS = {
    "ko": {"active": "사용 중", "optional": "선택", "test": "개발·테스트"},
    "en": {"active": "Active", "optional": "Optional", "test": "Test"},
}

ROLE_KO = {
    "UI runtime": "UI 런타임",
    "Build and dev server": "빌드·개발 서버",
    "Type system": "타입 시스템",
    "Icons": "아이콘",
    "Markdown rendering": "Markdown 렌더링",
    "GFM tables and task lists": "GFM 표·작업 목록",
    "Line-break handling": "줄바꿈 처리",
    "Static serving and API proxy": "정적 파일 서빙·API 프록시",
    "Web build runtime": "웹 빌드 런타임",
    "API runtime": "API 런타임",
    "HTTP API framework": "HTTP API 프레임워크",
    "ASGI server": "ASGI 서버",
    "Request and response validation": "요청·응답 검증",
    "Model server HTTP client": "모델 서버 HTTP 클라이언트",
    "PostgreSQL driver": "PostgreSQL 드라이버",
    "Redis client": "Redis 클라이언트",
    "System metrics": "시스템 메트릭",
    "Container status cards": "컨테이너 상태 카드",
    "Vector database": "벡터 데이터베이스",
    "Vector client": "벡터 클라이언트",
    "Multilingual embeddings": "다국어 임베딩",
    "Embedding inference": "임베딩 추론",
    "Sentence chunking": "문장 청킹",
    "Audit and metadata store": "감사·메타데이터 저장소",
    "Rate limiting and cache": "속도 제한·캐시",
    "Vector database alternative": "벡터 DB 대안",
    "BSD Redis alternative": "BSD Redis 대안",
    "Policy and retrieval graph": "정책·검색 그래프",
    "LLM tracing and evaluation": "LLM 트레이스·평가",
    "User memory": "사용자 메모리",
    "Local model runtime": "로컬 모델 런타임",
    "GPU model serving": "GPU 모델 서빙",
    "Model gateway alternative": "모델 게이트웨이 대안",
    "High-throughput serving alternative": "고처리량 서빙 대안",
    "HWP/HWPX/HWPML parser sidecar": "HWP/HWPX/HWPML 파서 사이드카",
    "PDF/Office/image/HTML parser sidecar": "PDF·Office·이미지·HTML 파서 사이드카",
    "Legacy Office/RTF/ZIP fallback": "레거시 Office·RTF·ZIP 폴백",
    "Container runtime": "컨테이너 런타임",
    "Deployment manifests": "배포 매니페스트",
    "Browser E2E tests": "브라우저 E2E 테스트",
    "Python tests": "Python 테스트",
    "PR vulnerability and secret scan": "PR 취약점·비밀값 스캔",
}

NOTE_KO = {
    "redis": "배포 버전에 따라 라이선스가 다릅니다. 버전을 고정하고 확인하세요. Valkey는 BSD 대안입니다.",
    "langfuse": "/ee 디렉터리의 일부 엔터프라이즈 기능은 상용 라이선스입니다. 코어는 MIT입니다.",
    "docker": "Docker Desktop은 별도 상용 약관이 적용될 수 있습니다.",
}


def load_components() -> list[dict]:
    spec = importlib.util.spec_from_file_location("baryon_open_source", INVENTORY)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {INVENTORY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.COMPONENTS)


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def license_family(license_text: str) -> str:
    if license_text.startswith("MIT"):
        return "MIT"
    if license_text.startswith("Apache"):
        return "Apache-2.0"
    if license_text.startswith("BSD"):
        return "BSD"
    if license_text.startswith("LGPL"):
        return "LGPL-3.0"
    return "other"


def render_summary(components: list[dict], lang: str) -> str:
    counts = collections.Counter(component["license"] for component in components)
    statuses = collections.Counter(component["status"] for component in components)
    labels = {
        "ko": ("전체 컴포넌트", "사용 중", "선택", "개발·테스트", "라이선스는 업스트림 표기를 그대로 사용합니다. 복합 라이선스(Redis, Langfuse)는 아래 주의사항을 함께 확인하세요."),
        "en": ("Total components", "Active", "Optional", "Test", "License strings follow the upstream projects. For compound licenses (Redis, Langfuse), also read the caveats below."),
    }[lang]
    chips = "".join(
        f'<span class="oss-chip"><b>{escape(license_name)}</b><span>{count}</span></span>'
        for license_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return f"""
      <div class="oss-stats">
        <div class="oss-stat"><b>{len(components)}</b><span>{labels[0]}</span></div>
        <div class="oss-stat"><b>{statuses['active']}</b><span>{labels[1]}</span></div>
        <div class="oss-stat"><b>{statuses['optional']}</b><span>{labels[2]}</span></div>
        <div class="oss-stat"><b>{statuses['test']}</b><span>{labels[3]}</span></div>
      </div>
      <div class="oss-license-chips" aria-label="License summary">{chips}</div>
      <p class="oss-summary-note">{escape(labels[4])}</p>
    """


def render_groups(components: list[dict], lang: str) -> str:
    headers = {
        "ko": ("컴포넌트", "역할", "라이선스", "상태"),
        "en": ("Component", "Role", "License", "Status"),
    }[lang]
    blocks: list[str] = []
    for group in GROUP_ORDER:
        items = [component for component in components if component["group"] == group]
        if not items:
            continue
        rows: list[str] = []
        for component in items:
            role = ROLE_KO.get(component["role"], component["role"]) if lang == "ko" else component["role"]
            note = NOTE_KO.get(component["id"], component.get("note", "")) if lang == "ko" else component.get("note", "")
            note_html = f'<small class="oss-note">{escape(note)}</small>' if note else ""
            status = STATUS_LABELS[lang][component["status"]]
            family = license_family(component["license"])
            rows.append(
                f'<tr data-name="{escape(component["name"])}" data-status="{escape(component["status"])}" '
                f'data-license="{escape(component["license"])}" data-license-family="{escape(family)}">'
                f'<td><a class="oss-name" href="{escape(component["url"])}" target="_blank" rel="noreferrer"><strong>{escape(component["name"])}</strong></a>{note_html}</td>'
                f'<td>{escape(role)}</td>'
                f'<td><span class="oss-license">{escape(component["license"])}</span></td>'
                f'<td><span class="oss-badge {escape(component["status"])}">{escape(status)}</span></td>'
                '</tr>'
            )
        label = GROUP_LABELS[lang][group]
        blocks.append(
            f"""
      <section class="oss-group" data-group="{escape(group)}">
        <div class="oss-group-head"><h2>{escape(label)}</h2><span>{len(items)}</span></div>
        <div class="table-wrap">
          <table class="oss-table">
            <thead><tr>{''.join(f'<th>{escape(header)}</th>' for header in headers)}</tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
      </section>"""
        )
    return "\n".join(blocks)


def replace_block(text: str, start: str, end: str, content: str, path: Path) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise SystemExit(f"{path}: missing markers {start} / {end}")
    return pattern.sub(start + "\n" + content + "\n" + end, text, count=1)


def main() -> int:
    components = load_components()
    total = len(components)
    pages = {
        "ko": SITE / "open-source" / "index.html",
        "en": SITE / "en" / "open-source" / "index.html",
    }
    for lang, path in pages.items():
        if not path.exists():
            raise SystemExit(f"{path}: page template not found")
        text = path.read_text(encoding="utf-8")
        text = text.replace("<!-- OSS_TOTAL -->", str(total))
        text = re.sub(r'(<b id="oss-result-count">).*?(</b>)', rf'\g<1>{total}\g<2>', text, count=1)
        text = replace_block(text, "<!-- OSS_SUMMARY_START -->", "<!-- OSS_SUMMARY_END -->", render_summary(components, lang), path)
        text = replace_block(text, "<!-- OSS_GROUPS_START -->", "<!-- OSS_GROUPS_END -->", render_groups(components, lang), path)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} ({total} components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
