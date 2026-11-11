import assert from "node:assert/strict";

import { chromium } from "playwright";

const baseUrl = (process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173").replace(/\/$/, "");
const documentName = `e2e-${Date.now()}.md`;
const documentBody = "Baryon AX는 Qdrant와 LangGraph를 사용합니다.";

const healthResponse = await fetch(`${baseUrl}/api/health`).catch(() => null);
assert.ok(
  healthResponse?.ok,
  `Cannot reach ${baseUrl}/api/health. Start the API (port 8000) and the web dev server (port 5173) first.`,
);
const health = await healthResponse.json();
assert.equal(health.status, "ok");
const memoryEnabled = health.memory_backend === "mem0";

let browser;
try {
  browser = await chromium.launch();
} catch (error) {
  throw new Error("Playwright Chromium is not installed. Run: npx playwright install chromium", { cause: error });
}

const context = await browser.newContext({ locale: "ko-KR", viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
const consoleErrors = [];
page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
page.on("pageerror", error => consoleErrors.push(String(error)));
const checks = [];

async function step(name, action) {
  await action();
  checks.push(name);
  console.log(`PASS ${name}`);
}

try {
  await step("page loads in Korean", async () => {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.waitForSelector("text=Baryon AX");
    assert.equal((await page.locator("h1").innerText()).trim(), "근거 기반 채팅");
  });

  await step("document uploads from the chat composer", async () => {
    await page.locator("footer input[type=file]").setInputFiles({
      name: documentName,
      mimeType: "text/markdown",
      buffer: Buffer.from(documentBody),
    });
    await page.waitForFunction(() => document.body.innerText.includes("색인했습니다"), null, { timeout: 60000 });
  });

  await step("grounded chat streams an answer with sources", async () => {
    const composer = page.locator("textarea");
    await composer.fill("first line");
    await composer.press("Enter");
    await page.waitForFunction(() => document.querySelector("textarea")?.value === "first line\n");
    await composer.fill("Qdrant는 무엇에 사용하나요?");
    await composer.press("Shift+Enter");
    await page.waitForFunction(() => {
      const messages = [...document.querySelectorAll("article.assistant .message-body p")];
      const last = messages.at(-1);
      return Boolean(last?.textContent?.trim());
    }, null, { timeout: 60000 });
    await page.waitForSelector(".sources button", { timeout: 30000 });
    assert.ok((await page.locator(".sources button").count()) >= 1);
    await page.waitForSelector("article.assistant .trace summary", { timeout: 30000 });
    assert.equal(await page.locator("article.assistant .trace[open]").count(), 0);
    await page.locator("article.assistant .trace summary").last().click();
    await page.waitForSelector("article.assistant .trace li", { timeout: 10000 });
    assert.ok((await page.locator("article.assistant .trace li").count()) >= 4);
    const traceText = await page.locator("article.assistant .trace").last().innerText();
    assert.match(traceText, /메모리 검색/);
    assert.match(traceText, /문서 검색/);
  });

  await step("citation opens the cited passage and original document", async () => {
    await page.locator(".sources button").first().click();
    await page.waitForSelector(".source-panel", { timeout: 10000 });
    await page.waitForSelector(".source-original .markdown", { timeout: 10000 });
    const panel = await page.locator(".source-panel").innerText();
    assert.match(panel, /문서 원문/);
    if (await page.locator(".source-chunk p").count()) {
      assert.ok((await page.locator(".source-chunk p").first().innerText()).trim().length > 0);
    }
    assert.ok((await page.locator(".source-original .markdown").innerText()).trim().length > 0);
    await page.locator(".source-close").click();
    await page.waitForSelector(".source-panel", { state: "detached", timeout: 5000 });
  });

  await step("markdown renders in user messages", async () => {
    await page.locator("textarea").fill("**Markdown bold** 확인");
    await page.locator("textarea").press("Shift+Enter");
    await page.waitForFunction(() => {
      return [...document.querySelectorAll("article.user .markdown strong")].some(element => element.textContent === "Markdown bold");
    }, null, { timeout: 20000 });
  });

  await step("member role is blocked from the audit log", async () => {
    await page.getByRole("button", { name: "거버넌스", exact: true }).click();
    await page.waitForFunction(() => document.body.innerText.includes("관리자 역할이 필요합니다"), null, { timeout: 10000 });
    assert.equal(await page.locator('select[aria-label="역할"]').inputValue(), "member");
  });

  await step("administrator role reads organization audit events", async () => {
    await page.locator('select[aria-label="역할"]').selectOption("admin");
    await page.waitForFunction(() => document.body.innerText.includes("document.ingested"), null, { timeout: 10000 });
    assert.ok((await page.locator(".event").count()) >= 1);
  });

  await step("administrator settings show runtime and policy", async () => {
    await page.locator("nav").getByRole("button", { name: "설정", exact: true }).click();
    await page.waitForSelector(".setting-card", { timeout: 10000 });
    assert.ok((await page.locator(".setting-card").count()) >= 4);
    const text = await page.locator(".settings-grid").innerText();
    assert.match(text, /0\.6\.0/);
    assert.match(text, /Qdrant \+ FastEmbed/);
    assert.match(text, /postgresql/i);
    assert.match(text, /전화번호 마스킹/);
  });

  await step("administrator settings show live system metrics and links", async () => {
    await page.waitForSelector(".system-card", { timeout: 10000 });
    assert.ok((await page.locator(".system-card").count()) >= 4);
    assert.ok((await page.locator(".meter").count()) >= 2);
    assert.ok((await page.locator(".container-item").count()) >= 1);
    const text = await page.locator(".settings-grid").innerText();
    assert.match(text, /저장공간/);
    assert.match(text, /메모리/);
    assert.match(text, /프로세스/);
    assert.match(text, /컨테이너/);
    const links = page.locator(".link-grid a");
    assert.ok((await links.count()) >= 1);
    const href = await links.first().getAttribute("href");
    assert.match(href ?? "", /^http/);
  });

  if (memoryEnabled) {
    await step("memory layer stores and searches preferences", async () => {
      await page.locator("nav").getByRole("button", { name: "메모리", exact: true }).click();
      await page.locator(".memory-form input").fill("사용자는 Qdrant와 LangGraph를 선호하고 한국어 답변을 원합니다.");
      await page.getByRole("button", { name: "메모리 저장", exact: true }).click();
      await page.waitForFunction(() => document.querySelectorAll(".memory-item").length > 0, null, { timeout: 60000 });
      await page.locator(".memory-search input").fill("Qdrant");
      await page.getByRole("button", { name: "검색", exact: true }).click();
      await page.waitForFunction(() => document.body.innerText.includes("Qdrant"), null, { timeout: 30000 });
    });
  } else {
    console.log("SKIP memory layer (Mem0 disabled)");
  }

  await step("knowledge library lists the uploaded document", async () => {
    await page.locator("nav").getByRole("button", { name: "지식", exact: true }).click();
    await page.waitForFunction(name => document.body.innerText.includes(name), documentName, { timeout: 10000 });
  });

  await step("knowledge page lists supported upload formats", async () => {
    await page.waitForSelector(".upload-zone", { timeout: 10000 });
    assert.ok((await page.locator(".format-chip").count()) >= 12);
    const formats = await page.locator(".upload-zone").innerText();
    for (const label of ["PDF", "HWP", "HWPX", "Word", "Excel", "CSV", "Markdown", "PNG", "JPG"]) {
      assert.ok(formats.includes(label), `missing format chip: ${label}`);
    }
  });

  await step("administrator edits document roles", async () => {
    const row = page.locator(".document", { hasText: documentName });
    await row.getByRole("button", { name: "권한 편집" }).click();
    await row.getByLabel("허용 역할").fill("member, admin, legal");
    await row.getByRole("button", { name: "저장" }).click();
    await row.getByText("문서 권한을 저장했습니다.").waitFor({ timeout: 10000 });
    await row.getByText("legal").waitFor({ timeout: 10000 });
    assert.match(await row.innerText(), /legal/);
  });

  await step("settings and ACL controls are hidden from members", async () => {
    await page.locator('select[aria-label="역할"]').selectOption("member");
    await page.waitForTimeout(300);
    assert.equal(await page.locator("nav").getByRole("button", { name: "설정", exact: true }).count(), 0);
    assert.equal(await page.locator(".admin-badge").count(), 0);
    assert.equal(await page.locator(".document", { hasText: documentName }).getByRole("button", { name: "권한 편집" }).count(), 0);
  });

  assert.deepEqual(consoleErrors, [], `Unexpected browser console errors:\n${consoleErrors.join("\n")}`);
  checks.push("no browser console errors");
  console.log("PASS no browser console errors");

  if (process.env.E2E_SCREENSHOT) {
    await page.screenshot({ path: process.env.E2E_SCREENSHOT, fullPage: true });
    console.log(`Screenshot: ${process.env.E2E_SCREENSHOT}`);
  }
} finally {
  await browser.close();
  // Remove documents created by E2E runs so demos and retrieval stay clean.
  try {
    const adminHeaders = { "X-User-Id": "local-user", "X-Organization-Id": "local-org", "X-Roles": "admin" };
    const list = await fetch(`${baseUrl}/api/v1/documents`, { headers: adminHeaders });
    if (list.ok) {
      const documents = (await list.json()).data;
      for (const document of documents.filter(item => item.title.startsWith("e2e-"))) {
        await fetch(`${baseUrl}/api/v1/documents/${document.id}`, { method: "DELETE", headers: adminHeaders });
      }
    }
  } catch {
    console.warn("E2E cleanup skipped");
  }
}

console.log(`\n${checks.length} E2E checks passed against ${baseUrl}`);
