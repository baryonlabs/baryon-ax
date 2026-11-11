import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const baseUrl = (process.env.E2E_BASE_URL ?? "http://127.0.0.1:3101").replace(/\/$/, "");
const api = `${baseUrl}/api`;
const model = process.env.E2E_MODEL ?? "baryon-ax-qwen";
const outputDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/images/gallery");

const userHeaders = { "X-User-Id": "local-user", "X-Organization-Id": "local-org", "X-Roles": "member" };
const adminHeaders = { ...userHeaders, "X-Roles": "admin" };
const documentTitle = "Baryon AX 플랫폼 개요.md";

const documentBody = `# Baryon AX 플랫폼 개요

Baryon AX는 사내 폐쇄망에서 실행되는 온프레미스 엔터프라이즈 AI 플랫폼입니다.
모델, 문서, 권한, 감사, 메모리를 하나의 운영 모델로 묶고 근거 기반 답변을 제공합니다.

## 구성 요소

- FastAPI 기반 API가 채팅, 문서 수집, 검색, 감사 이벤트를 담당합니다.
- React 기반 웹 UI가 채팅, 지식 라이브러리, 메모리, 거버넌스, 관리자 설정을 제공합니다.
- Qdrant가 문서 청크와 사용자 기억의 벡터를 저장합니다.
- LangGraph가 정책 검사, 메모리 검색, 문서 검색 순서를 오케스트레이션합니다.
- PostgreSQL은 감사 이벤트를, Redis는 사용자별 요청 제한을 담당합니다.
- Langfuse는 대화 추적을, Mem0는 사용자 장기 기억을 담당합니다.

## 보안 원칙

- 모든 문서와 기억은 조직 단위로 격리하고 역할 기반 ACL을 적용합니다.
- 채팅 답변에는 권한 범위 안에서 검색된 문서만 출처로 표시합니다.
- 관리자만 감사 로그, 지식 공백, 시스템 설정에 접근할 수 있습니다.
- 원본 파일과 문서 원문은 동일한 권한 검사를 통과한 사용자에게만 제공합니다.

## 처리 과정

질문이 들어오면 속도 제한, 정책 검사, 메모리 검색, 문서 검색, 모델 생성, 감사 기록 순서로 처리합니다.
각 단계의 소요 시간은 답변 아래 처리 과정 패널과 서버 로그에 함께 기록됩니다.`;

const seededQuestions = [
  "2027년 제품 로드맵과 출시 일정은 어떻게 되나요?",
  "사내 보안 정책의 예외 승인 절차는 무엇인가요?",
];

async function jsonFetch(url, options = {}, headers = adminHeaders) {
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers ?? {}) } });
  if (!response.ok) throw new Error(`${options.method ?? "GET"} ${url} -> ${response.status}`);
  return response.json();
}

async function seed() {
  const existing = await jsonFetch(`${api}/v1/documents`, {}, userHeaders);
  for (const document of existing.data.filter(item => item.title === documentTitle)) {
    await fetch(`${api}/v1/documents/${document.id}`, { method: "DELETE", headers: userHeaders });
  }

  const form = new FormData();
  form.append("file", new Blob([documentBody], { type: "text/markdown" }), documentTitle);
  form.append("allowed_roles", "member,admin");
  const uploaded = await jsonFetch(`${api}/v1/documents`, { method: "POST", body: form }, userHeaders);

  const memories = await jsonFetch(`${api}/v1/memories`, {}, userHeaders);
  if (!memories.data.some(item => item.memory.includes("한국어 답변과 출처"))) {
    await fetch(`${api}/v1/memories`, {
      method: "POST",
      headers: { ...userHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ text: "사용자는 한국어 답변과 문서 출처 표시를 선호한다.", metadata: { source: "gallery" } }),
    });
  }

  const gaps = await jsonFetch(`${api}/v1/admin/gaps?status=all`, {}, adminHeaders);
  for (const gap of gaps.data) {
    if (/Markdown bold|커피|Qdrant는 무엇에 사용하나요/.test(gap.question)) {
      await fetch(`${api}/v1/admin/gaps/${gap.id}/dismiss`, { method: "POST", headers: adminHeaders });
    }
  }

  for (const question of seededQuestions) {
    await fetch(`${api}/v1/chat/completions`, {
      method: "POST",
      headers: { ...userHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: [{ role: "user", content: question }], stream: false, use_memory: true }),
    });
  }

  const entries = await jsonFetch(`${api}/v1/tacit/entries`, {}, userHeaders);
  if (!entries.data.some(item => item.question.includes("해외 지사"))) {
    const questions = await jsonFetch(`${api}/v1/tacit/questions`, {}, userHeaders);
    const source = questions.data.find(item => item.source === "gap") ?? questions.data[0];
    await fetch(`${api}/v1/tacit/entries`, {
      method: "POST",
      headers: { ...userHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({
        question: source.question,
        answer: "2030년까지 동남아 2개 지사(싱가포르·호치민)를 설립합니다. 초기 예산은 지사당 12억원, 총 24억원이며 본사 전략기획팀이 분기별 집행률을 점검합니다.",
        gap_id: source.gap_id,
        source: source.source,
      }),
    });
  }

  return uploaded.id;
}

async function main() {
  await mkdir(outputDir, { recursive: true });
  const documentId = await seed();

  const browser = await chromium.launch();
  const page = await browser.newPage({ locale: "ko-KR", viewport: { width: 1440, height: 950 } });
  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.locator('select[aria-label="역할"]').selectOption("admin");
    await page.waitForTimeout(300);

    await page.locator("textarea").fill("Baryon AX의 구성 요소와 보안 원칙을 알려줘.");
    await page.locator("textarea").press("Shift+Enter");
    await page.waitForSelector(".sources button", { timeout: 180_000 });
    await page.waitForFunction(() => {
      const time = document.querySelector("article.assistant .trace summary em")?.textContent ?? "";
      return time && time !== "…";
    }, null, { timeout: 120_000 });
    await page.locator("article.assistant .trace summary").last().click();
    await page.waitForSelector("article.assistant .trace li");
    await page.screenshot({ path: path.join(outputDir, "01-chat.png"), fullPage: true });
    console.log("captured 01-chat.png");

    await page.locator(".sources button").first().click();
    await page.waitForSelector(".source-original .markdown", { timeout: 30_000 });
    await page.screenshot({ path: path.join(outputDir, "02-source-viewer.png") });
    console.log("captured 02-source-viewer.png");
    await page.locator(".source-close").click();

    await page.locator("nav").getByRole("button", { name: "지식", exact: true }).click();
    await page.waitForSelector(".upload-zone");
    await page.waitForFunction(title => document.body.innerText.includes(title), documentTitle, { timeout: 15_000 });
    await page.screenshot({ path: path.join(outputDir, "03-knowledge.png") });
    console.log("captured 03-knowledge.png");

    await page.locator("nav").getByRole("button", { name: "메모리", exact: true }).click();
    await page.waitForSelector(".memory-item", { timeout: 30_000 });
    await page.screenshot({ path: path.join(outputDir, "04-memory.png") });
    console.log("captured 04-memory.png");

    await page.locator("nav").getByRole("button", { name: "암묵지", exact: true }).click();
    await page.waitForSelector(".tacit-basis", { timeout: 20_000 });
    await page.locator(".tacit-basis summary").first().click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(outputDir, "08-tacit-knowledge.png"), fullPage: true });
    console.log("captured 08-tacit-knowledge.png");

    await page.locator("nav").getByRole("button", { name: "거버넌스", exact: true }).click();
    await page.waitForSelector(".event", { timeout: 15_000 });
    await page.screenshot({ path: path.join(outputDir, "05-governance.png") });
    console.log("captured 05-governance.png");

    await page.locator("nav").getByRole("button", { name: "지식 공백", exact: true }).click();
    await page.waitForSelector(".gap-summary", { timeout: 15_000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(outputDir, "06-knowledge-gaps.png") });
    console.log("captured 06-knowledge-gaps.png");

    await page.locator("nav").getByRole("button", { name: "설정", exact: true }).click();
    await page.waitForSelector(".system-card", { timeout: 15_000 });
    await page.waitForSelector(".container-item", { timeout: 15_000 });
    await page.screenshot({ path: path.join(outputDir, "07-settings.png") });
    console.log("captured 07-settings.png");

    console.log(`Document ${documentId} and gallery memory kept for demo use.`);
  } finally {
    await browser.close();
  }
}

await main();
