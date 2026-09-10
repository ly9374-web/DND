const { chromium, expect } = require(process.env.PLAYWRIGHT_NODE_MODULE || "playwright/test");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    const base = process.env.TEST_URL || "http://localhost:8517";
    await page.goto(base + "?full=1&resend=1");
    await page.waitForLoadState("networkidle");
    const input = page.locator(".st-key-page2_chat_input textarea");
    const menu = page.getByRole("listbox", { name: "快捷输入", exact: true });
    const messages = page.locator('[data-testid="stChatMessage"]');
    await expect(messages).toHaveCount(2);
    await input.press("/");
    await page.keyboard.insertText("重发");
    await expect(menu.getByRole("option", { name: "重发 · 操作" })).toBeVisible();
    await input.press("Enter");
    await expect(input).toHaveValue("/重发");
    await expect(messages).toHaveCount(2);
    await expect(messages.last()).toContainText("这是用于检查输入框位置的已有聊天。");
    await input.press("Enter");
    await expect(page.getByText(/正在执行：重发上一轮/)).toBeVisible();
    await expect(messages.last()).toContainText("这是用于检查输入框位置的已有聊天。");
    await expect(messages.last()).toContainText("重发的新回复：查看房间", { timeout: 15000 });
    await expect(messages).toHaveCount(2);
    await expect(messages.first()).toContainText("查看房间");
    assert.ok(!(await messages.allTextContents()).join("").includes("/重发"));
    console.log("PASS dropdown only fills; explicit send replaces one round after success");

    await page.goto(base + "?full=1&resend=1&fail=1");
    await page.waitForLoadState("networkidle");
    const original = await messages.allTextContents();
    await input.fill("／重发");
    await input.press("Escape");
    await input.press("Enter");
    await expect(page.getByText(/重发失败，原问答和记忆已保留/)).toBeVisible({ timeout: 15000 });
    assert.deepEqual(await messages.allTextContents(), original);
    await page.screenshot({ path: "/tmp/dnd-resend-failure-preserved.png", fullPage: true });
    console.log("PASS full-width command and failed request preserve original conversation");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
