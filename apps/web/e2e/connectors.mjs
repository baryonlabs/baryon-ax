/**
 * Connector dashboard role test.
 *
 *   cd apps/web && node e2e/connectors.mjs
 *   E2E_BASE_URL=http://127.0.0.1:3101 node e2e/connectors.mjs
 *
 * Admin must see the full catalog; a member must see fewer connectors/tools
 * and approval badges for write tools. Requires the API catalog endpoints.
 */

import { chromium } from "playwright";
import { tmpdir } from "node:os";
import path from "node:path";

const BASE = process.env.E2E_BASE_URL || "http://localhost:3001";
const browser = await chromium.launch();
const results = {};
const failures = [];

async function inspect(role) {
  const context = await browser.newContext({ locale: "ko-KR", viewport: { width: 1440, height: 1100 } });
  await context.addInitScript(value => localStorage.setItem("baryon-role", value), role);
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", error => consoleErrors.push(String(error)));

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "연결", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll(".os-row").length > 0, null, { timeout: 15000 });

  const rows = await page.locator(".os-row").count();
  const tools = await page.locator(".connector-tools .status-pill").count();
  const approvals = await page.getByText("승인 필요", { exact: false }).count();
  const header = (await page.locator(".knowledge").innerText()).includes("MCP 커넥터");
  const screenshot = path.join(tmpdir(), `baryon-connectors-${role}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  await context.close();
  return { role, rows, tools, approvals, header, consoleErrors, screenshot };
}

try {
  results.admin = await inspect("admin");
  results.member = await inspect("member");

  if (!results.admin.header || !results.member.header) failures.push("connector dashboard header missing");
  if (results.admin.rows < results.member.rows) failures.push("admin should see at least as many connectors as a member");
  if (results.admin.tools <= results.member.tools) failures.push("admin should see more tools than a member");
  if (results.member.approvals === 0) failures.push("member should see approval-required write tools");
  if (results.admin.approvals !== 0) failures.push("admin write tools should not require approval");
  for (const role of ["admin", "member"]) {
    if (results[role].consoleErrors.length) failures.push(`${role} console errors: ${results[role].consoleErrors.join(" | ")}`);
  }
} finally {
  await browser.close();
}

console.log(JSON.stringify({ base: BASE, results, failures }, null, 2));
if (failures.length) {
  console.error(`connector dashboard failed: ${failures.join("; ")}`);
  process.exit(1);
}
