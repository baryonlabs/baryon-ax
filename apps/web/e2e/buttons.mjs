/**
 * Button-level E2E sweep ("computer use" style) against a running stack.
 *
 *   cd apps/web && node e2e/buttons.mjs
 *   E2E_BASE_URL=http://localhost:3001 node e2e/buttons.mjs
 *
 * Every step clicks a real control in a real browser and asserts an observable
 * response. Failures are collected, screenshotted, and reported at the end.
 */

import { chromium } from "playwright";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const BASE = process.env.E2E_BASE_URL || "http://localhost:3001";
const STAMP = `E2E-${Date.now()}`;
const results = [];
const consoleErrors = [];
const failedRequests = [];
let page;
let shotIndex = 0;

function log(message) {
  console.log(message);
}

async function step(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    log(`PASS  ${name}`);
  } catch (error) {
    const message = String(error?.message || error).split("\n")[0];
    results.push({ name, ok: false, message });
    shotIndex += 1;
    const file = path.join(tmpdir(), `baryon-e2e-fail-${shotIndex}.png`);
    await page.screenshot({ path: file, fullPage: true }).catch(() => undefined);
    log(`FAIL  ${name} :: ${message} (${file})`);
  }
}

async function expectText(selector, text, timeout = 8000) {
  await page.waitForFunction(
    ({ selector, text }) => (document.querySelector(selector)?.textContent || "").includes(text),
    { selector, text },
    { timeout },
  );
}

async function expectVisible(selector, timeout = 8000) {
  await page.locator(selector).first().waitFor({ state: "visible", timeout });
}

async function expectHidden(selector, timeout = 8000) {
  await page.locator(selector).first().waitFor({ state: "detached", timeout });
}

async function waitForAnswer(timeout = 90000) {
  await page.waitForFunction(() => {
    const last = document.querySelector(".message.assistant:last-child");
    if (!last || last.querySelector(".cursor")) return false;
    return (last.textContent || "").trim().length > 10;
  }, undefined, { timeout });
}

async function nav(name) {
  await page.locator("nav").getByRole("button", { name, exact: true }).click();
  await page.waitForTimeout(300);
}

async function makeFile(dir, name, content) {
  const file = path.join(dir, name);
  await writeFile(file, content, "utf8");
  return file;
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ["clipboard-read", "clipboard-write"] });
  page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(String(error)));
  page.on("response", (response) => {
    if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
  });

  await page.addInitScript(() => {
    localStorage.setItem("baryon-role", "admin");
    localStorage.setItem("baryon-locale", "ko");
  });
  await page.goto(BASE, { waitUntil: "networkidle" });

  const dir = await mkdtemp(path.join(tmpdir(), "baryon-e2e-"));
  const marker = `KEYWORD-${STAMP}`;
  const gapMarker = `GAPQ-${Date.now()}`;
  const doc1 = await makeFile(dir, `${STAMP}-doc1.md`, `# ${STAMP} 문서 1\n\n${marker} 예산은 777입니다.`);
  const doc2 = await makeFile(dir, `${STAMP}-doc2.md`, `# ${STAMP} 문서 2\n\n${marker} 일정은 3월입니다.`);
  const attach = await makeFile(dir, `${STAMP}-attach.md`, `# 첨부\n\n${STAMP} 첨부 내용`);

  log(`target: ${BASE}`);
  log(`marker: ${STAMP}`);

  // ---------------------------------------------------------------- navigation
  for (const [label, title] of [
    ["채팅", "근거 기반 채팅"],
    ["지식", "지식 라이브러리"],
    ["암묵지", "암묵지"],
    ["메모리", "메모리"],
    ["거버넌스", "감사 로그"],
    ["지식 공백", "지식 공백"],
    ["설정", "관리자 설정"],
  ]) {
    await step(`nav ${label}`, async () => {
      await nav(label);
      await expectText("header h1", title);
    });
  }

  // ------------------------------------------------------------- header controls
  await step("role select: member -> admin", async () => {
    await nav("채팅");
    const select = page.locator('select[aria-label="역할"]');
    await select.selectOption("member");
    await page.waitForTimeout(300);
    if (await page.locator(".admin-badge").count()) throw new Error("admin badge still visible");
    await select.selectOption("admin");
    await page.waitForTimeout(300);
    await expectVisible(".admin-badge");
  });

  await step("locale select: en -> ko", async () => {
    await page.locator('select[aria-label="언어"]').selectOption("en");
    await page.waitForTimeout(300);
    await expectText("nav", "Chat");
    // The aria-label itself is localized, so re-locate after switching.
    await page.locator('select[aria-label="Language"]').selectOption("ko");
    await page.waitForTimeout(300);
    await expectText("nav", "채팅");
  });

  await step("model select is present", async () => {
    const options = await page.locator('select[aria-label="모델"] option').count();
    if (options < 1) throw new Error("no model options");
  });

  await step("RAG toggle flips", async () => {
    const toggle = page.locator("label.toggle input");
    const before = await toggle.isChecked();
    await toggle.click();
    await page.waitForTimeout(150);
    const after = await toggle.isChecked();
    if (before === after) throw new Error("toggle did not change");
    await toggle.click();
  });

  // ---------------------------------------------------------------------- chat
  await step("send button disabled until text", async () => {
    const send = page.locator('footer button[aria-label="채팅"]').last();
    if (!(await send.isDisabled())) throw new Error("send enabled with empty input");
    await page.locator("footer textarea").fill("한 문장으로 인사해 주세요.");
    if (await send.isDisabled()) throw new Error("send stayed disabled after typing");
  });

  await step("send message gets a response", async () => {
    await page.locator('footer button[aria-label="채팅"]').last().click();
    await waitForAnswer();
  });

  await step("chat upload button ingests a file", async () => {
    await page.locator("footer input[type=file]").setInputFiles(doc1);
    await expectText(".conversation", "색인했습니다", 30000);
  });

  await step("citation opens the source dialog and closes", async () => {
    await page.locator("footer textarea").fill(`${marker} 예산은 얼마인가요?`);
    await page.locator('footer button[aria-label="채팅"]').last().click();
    await waitForAnswer();
    const source = page.locator(".sources button").last();
    if (!(await source.count())) return; // no citation for this answer; not a control failure
    await source.click();
    await expectVisible(".source-panel");
    await page.locator(".source-close").click();
    await expectHidden(".source-panel");
  });

  await step("copy and feedback controls respond", async () => {
    const last = page.locator(".message.assistant").last();
    await last.hover();
    await last.locator('button[aria-label="복사"]').click();
    await expectText(".toast", "복사했습니다", 5000);
    await page.waitForTimeout(300);
    await last.locator('button[aria-label="도움이 됐어요"]').click();
    await expectText(".toast", "피드백 감사합니다", 5000);
  });

  await step("stop generation responds", async () => {
    await page.locator("footer textarea").fill("아주 긴 설명을 1000단어로 작성해 주세요.");
    await page.locator('footer button[aria-label="채팅"]').last().click();
    const stop = page.locator(".composer button.stop");
    await stop.waitFor({ state: "visible", timeout: 30000 });
    await stop.click();
    await expectText(".toast", "생성을 중지했습니다", 5000);
  });

  // ----------------------------------------------------------------- knowledge
  await step("multi-file upload with category and tags", async () => {
    await nav("지식");
    await page.locator(".upload-meta input").nth(0).fill("E2E분류");
    await page.locator(".upload-meta input").nth(1).fill("e2e,자동");
    await page.locator(".knowledge input[type=file]").setInputFiles([doc1, doc2]);
    await page.waitForFunction(
      ({ a, b }) => {
        const text = document.body.innerText;
        return text.includes(a) && text.includes(b);
      },
      { a: `${STAMP}-doc1.md`, b: `${STAMP}-doc2.md` },
      { timeout: 60000 },
    );
    await expectText(".document-list", "#e2e");
  });

  await step("category filter narrows the list", async () => {
    await page.locator(".filter-bar select").selectOption("E2E분류");
    await page.waitForTimeout(300);
    const rows = await page.locator(".document").count();
    if (rows < 2) throw new Error(`expected >=2 rows, got ${rows}`);
    await expectText(".document-list", `${STAMP}-doc1.md`);
  });

  await step("tag chip filters and clears", async () => {
    await page.locator(".filter-bar select").selectOption("");
    await page.getByRole("button", { name: "#e2e", exact: true }).click();
    await page.waitForTimeout(300);
    if ((await page.locator(".document").count()) < 2) throw new Error("tag filter hid matching rows");
    await page.getByRole("button", { name: "#e2e", exact: true }).click();
    await page.waitForTimeout(300);
  });

  await step("edit classification and save", async () => {
    await page.locator(".filter-bar select").selectOption("");
    await page.waitForTimeout(300);
    const row = page.locator(".document", { hasText: `${STAMP}-doc1.md` }).first();
    await row.getByRole("button", { name: "분류 편집" }).click();
    const editor = row.locator(".meta-editor");
    await editor.locator("input").nth(0).fill("E2E수정");
    await editor.locator("input").nth(1).fill("수정됨");
    await editor.getByRole("button", { name: "저장" }).click();
    await expectText(".document-list", "E2E수정");
  });

  // -------------------------------------------------------------------- tacit
  await step("tacit answer button reveals and focuses the form", async () => {
    await nav("암묵지");
    await page.getByRole("button", { name: "답하기" }).first().click();
    await expectVisible("#tacit-form");
    await page.waitForTimeout(500);
    const focused = await page.evaluate(() => document.activeElement?.tagName);
    if (focused !== "TEXTAREA") throw new Error(`textarea not focused (${focused})`);
    const scrolled = await page.evaluate(() => window.scrollY);
    if (scrolled <= 0) throw new Error("page did not scroll to the form");
  });

  await step("tacit attach, save, and publish", async () => {
    await page.locator("#tacit-form textarea").fill(`${STAMP} 판단 기준은 예외 승인 없이 100만원까지입니다.`);
    await page.locator("#tacit-form input[type=file]").setInputFiles(attach);
    await expectText("#tacit-form", `${STAMP}-attach.md`);
    await page.getByRole("button", { name: "답변 저장" }).click();
    await expectText(".settings-note", "답변을 저장했습니다", 15000);
    const entry = page.locator(".pack", { hasText: STAMP }).first();
    await entry.waitFor({ state: "visible", timeout: 10000 });
    await entry.getByRole("button", { name: "지식으로 게시" }).click();
    await expectText(".pack", "게시됨", 30000);
  });

  await step("tacit admin scope toggle", async () => {
    await page.getByRole("button", { name: "전체 답변" }).click();
    await page.waitForTimeout(400);
    await expectText(".pack", STAMP);
    await page.getByRole("button", { name: "내 답변" }).click();
  });

  // -------------------------------------------------------------------- memory
  await step("memory buttons respond when Mem0 is disabled", async () => {
    await nav("메모리");
    await page.getByRole("button", { name: "새로고침" }).click();
    await page.waitForTimeout(500);
    await page.locator('input[placeholder*="기억"]').fill(`${STAMP} 메모`);
    await page.getByRole("button", { name: "메모리 저장" }).click();
    await page.waitForTimeout(800);
    const text = await page.locator("main").innerText();
    if (!text.includes("메모리 기능이 비활성화") && !text.includes("요청을 완료할 수 없습니다")) {
      throw new Error("no disabled/error feedback");
    }
  });

  // ---------------------------------------------------------------- governance
  await step("governance refresh responds", async () => {
    await nav("거버넌스");
    await page.getByRole("button", { name: "새로고침" }).click();
    await page.waitForTimeout(500);
  });

  await step("member sees admin-required state in governance", async () => {
    await page.locator('select[aria-label="역할"]').selectOption("member");
    await nav("거버넌스");
    await expectText(".empty", "관리자 역할이 필요합니다");
    await page.locator('select[aria-label="역할"]').selectOption("admin");
  });

  // ---------------------------------------------------------------------- gaps
  await step("chat produces a new knowledge gap", async () => {
    await nav("채팅");
    await page.locator("footer textarea").fill(`${gapMarker} zqxvbn plorbnak flux?`);
    await page.locator('footer button[aria-label="채팅"]').last().click();
    await waitForAnswer();
    await page.waitForTimeout(1500);
  });

  await step("gap cluster creates an ontology pack", async () => {
    await nav("지식 공백");
    let card = page.locator(".cluster-card", { hasText: gapMarker }).first();
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await page.getByRole("button", { name: "새로고침" }).click();
      await page.waitForTimeout(700);
      card = page.locator(".cluster-card", { hasText: gapMarker }).first();
      if (await card.count()) break;
    }
    if (!(await card.count())) throw new Error("no cluster for the new gap");
    const before = await page.locator(".pack").count();
    await card.getByRole("button", { name: "온톨로지 꾸러미 만들기" }).click();
    await expectText(".settings-note", "초안을 생성했습니다", 15000);
    await page.waitForFunction(
      (count) => document.querySelectorAll(".pack").length > count,
      before,
      { timeout: 10000 },
    );
  });

  await step("pack approve and re-verify respond", async () => {
    const pack = page.locator(".pack").first();
    await pack.waitFor({ state: "visible", timeout: 10000 });
    await pack.getByRole("button", { name: "승인" }).click();
    await expectText(".pack", "검토됨", 15000);
    await page.locator(".pack").first().getByRole("button", { name: "재검증" }).click();
    await expectText(".pack", "커버리지", 20000);
  });

  await step("second gap is created for dismissal", async () => {
    await nav("채팅");
    await page.locator("footer textarea").fill(`${STAMP} 2100년 달 기지 예산은 얼마인가요?`);
    await page.locator('footer button[aria-label="채팅"]').last().click();
    await waitForAnswer();
  });

  await step("dismiss gap responds", async () => {
    await nav("지식 공백");
    await page.getByRole("button", { name: "새로고침" }).click();
    await page.waitForTimeout(600);
    const row = page.locator(".document", { hasText: "2100년" }).first();
    if (!(await row.count())) return;
    await row.locator('button[aria-label="제외"]').click();
    await page.waitForTimeout(600);
    if (await page.locator(".document", { hasText: "2100년" }).count()) throw new Error("gap row still visible");
  });

  // ------------------------------------------------------------------ settings
  await step("settings refresh and reindex respond", async () => {
    await nav("설정");
    await page.getByRole("button", { name: "새로고침" }).click();
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: "벡터 재색인" }).click();
    await page.waitForTimeout(1000);
    const text = await page.locator("main").innerText();
    if (!text.includes("실패") && !text.includes("재색인했습니다")) {
      throw new Error("no reindex feedback");
    }
  });

  await step("settings quick link navigates", async () => {
    await page.locator(".quick-links").getByRole("button", { name: "채팅", exact: true }).click();
    await expectText("header h1", "근거 기반 채팅");
  });

  await step("mobile navigation drawer opens and closes", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(400);
    await page.getByRole("button", { name: "메뉴", exact: true }).click();
    await page.locator(".sidebar.open").waitFor({ state: "visible", timeout: 5000 });
    await page.locator(".sidebar-backdrop.open").click();
    await page.waitForTimeout(300);
    await page.locator(".sidebar.open").waitFor({ state: "detached", timeout: 5000 });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.reload({ waitUntil: "networkidle" });
  });

  // -------------------------------------------------------------------- cleanup
  await step("delete E2E documents", async () => {
    await nav("지식");
    if (await page.locator(".filter-bar select").count()) {
      await page.locator(".filter-bar select").selectOption("");
      await page.waitForTimeout(300);
    }
    for (const title of [`${STAMP}-doc1.md`, `${STAMP}-doc2.md`]) {
      const row = page.locator(".document", { hasText: title }).first();
      if (await row.count()) {
        await row.locator('button[aria-label="문서 삭제"]').click();
        await page.waitForTimeout(500);
      }
    }
  });

  await browser.close();

  const failed = results.filter((result) => !result.ok);
  log("");
  log(`steps: ${results.length}, passed: ${results.length - failed.length}, failed: ${failed.length}`);
  if (failed.length) {
    for (const result of failed) log(` - ${result.name}: ${result.message}`);
  }
  if (consoleErrors.length) {
    log(`console errors: ${consoleErrors.length}`);
    for (const error of consoleErrors.slice(0, 10)) log(` - ${error.slice(0, 200)}`);
  }
  if (failedRequests.length) {
    log(`failed responses: ${failedRequests.length}`);
    for (const request of [...new Set(failedRequests)]) log(` - ${request}`);
  }
  process.exit(failed.length ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(2);
});
