// Run against tests/fixtures/quick_input_app.py with an isolated data directory.
const { chromium, expect } = require(process.env.PLAYWRIGHT_NODE_MODULE || "playwright/test");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto(process.env.TEST_URL || "http://localhost:8517");
    await page.waitForLoadState("networkidle");
    console.log((await page.locator("body").innerText()).slice(0, 1300));
    const input = page.locator(".st-key-page2_chat_input textarea");
    const menu = page.getByRole("listbox", { name: "快捷输入", exact: true });
    const sent = () => page.getByText("发送次数： 0", { exact: true });
    const waitBridge = (scope, busy) => page.waitForFunction(({ scope, busy }) =>
      [...document.querySelectorAll("iframe")].some(frame => {
        const document = frame.contentDocument;
        const text = document?.documentElement?.textContent || "";
        return document?.readyState === "complete" &&
          text.includes(`"scope": "${scope}:`) && text.includes(`const sendDisabled = ${busy};`);
      }), { scope, busy });
    await expect(input).toBeVisible();
    await page.waitForFunction(() => Boolean(window.__dndQuickInput && window.__dndEmptySpaceSubmitHandler));
    // Insert at a non-terminal caret position and consume Enter without submitting.
    await input.fill("前文后文");
    await input.press("Home");
    await input.press("ArrowRight");
    await input.press("ArrowRight");
    await input.press("/");
    await expect(menu).toBeVisible();
    await page.keyboard.insertText("场");
    await expect(menu.getByRole("option")).toHaveCount(1);
    await input.press("Enter");
    await expect(menu).toHaveCount(0);
    const expanded = await input.inputValue();
    assert.ok(expanded.startsWith("前文公开测试场景描述"));
    assert.ok(expanded.endsWith("后文"));
    await expect(sent()).toBeVisible();
    const caret = await input.evaluate(el => el.selectionStart);
    assert.equal(caret, expanded.length - 2);
    await page.getByRole("button", { name: "模拟后台刷新", exact: true }).click();
    await expect(input).toHaveValue(expanded);
    console.log("PASS middle insertion, search, no send, caret and rerun persistence");

    // A second insertion is supported; full-width slash and mouse selection too.
    await input.fill("原文");
    await input.press("End");
    await page.keyboard.insertText("／");
    await expect(menu).toBeVisible();
    await menu.getByRole("option").first().click();
    assert.ok((await input.inputValue()).startsWith("原文公开测试场景描述"));
    await input.press("/");
    await expect(menu).toBeVisible();
    await input.press("Enter");
    assert.equal((await input.inputValue()).split("公开测试场景描述").length, 3);
    console.log("PASS full-width slash, mouse and repeated insertion");

    await input.fill("https://example.com/a/b");
    await expect(menu).toHaveCount(0);
    await input.press("End");
    await input.press("/");
    await expect(menu).toBeVisible();
    await input.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(input).toHaveValue("https://example.com/a/b/");
    await input.fill("");
    await input.press("/");
    await page.keyboard.insertText("无此名称");
    await expect(menu).toHaveCount(0);
    for (let index = 0; index < 4; index += 1) await input.press("Backspace");
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("option")).toHaveCount(2);
    await input.press("Escape");
    console.log("PASS pasted URL, Escape and hidden empty results with Backspace recovery");

    // During an IME composition Enter belongs to the IME, not shortcut selection.
    await input.fill("");
    await input.press("/");
    await input.evaluate(el => {
      el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }));
    });
    await expect(menu).toBeVisible();
    await expect(input).toHaveValue("/");
    await input.evaluate(el => el.dispatchEvent(new CompositionEvent("compositionend", { data: "", bubbles: true })));
    await input.press("Escape");
    console.log("PASS IME Enter does not select or submit");

    // Space commits a Chinese IME candidate. A selectionchange can arrive after
    // compositionend but before the final input event and must not close the menu.
    await input.fill("");
    await input.press("/");
    await input.evaluate(el => {
      el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keydown", {
        key: " ", code: "Space", keyCode: 229, isComposing: true, bubbles: true,
      }));
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(el, "/场");
      el.setSelectionRange(2, 2);
      el.dispatchEvent(new CompositionEvent("compositionend", { data: "场", bubbles: true }));
      document.dispatchEvent(new Event("selectionchange", { bubbles: true }));
      el.dispatchEvent(new InputEvent("input", {
        inputType: "insertText", data: "场", isComposing: false, bubbles: true,
      }));
    });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("option")).toHaveCount(1);
    await input.press("Enter");
    assert.ok((await input.inputValue()).startsWith("公开测试场景描述"));

    // A real space outside IME composition remains a token boundary.
    await input.fill("");
    await input.press("/");
    await page.keyboard.insertText("场");
    await expect(menu).toBeVisible();
    await input.press("Space");
    await expect(menu).toHaveCount(0);
    await expect(input).toHaveValue("/场 ");
    console.log("PASS IME Space keeps filtering and ordinary Space closes the menu");

    await page.getByText("等待回复", { exact: true }).click();
    await waitBridge("False", true);
    await input.fill("");
    await input.press("/");
    await expect(menu).toBeVisible();
    await input.press("Enter");
    assert.ok((await input.inputValue()).startsWith("公开测试场景描述"));
    await input.press("Enter");
    await expect(sent()).toBeVisible();
    await page.getByText("等待回复", { exact: true }).click();
    await waitBridge("False", false);
    await expect(sent()).toBeVisible();
    console.log("PASS shortcut selection while waiting with submission blocked");

    await page.getByText("私密账号", { exact: true }).click();
    await waitBridge("True", false);
    await input.fill("");
    await input.press("/");
    await expect(menu.getByRole("option")).toHaveCount(3);
    await expect(menu.getByText("私密", { exact: true })).toBeVisible();
    await input.press("ArrowDown");
    await input.press("Enter");
    await expect(input).toHaveValue("私密测试内容");
    await page.getByText("私密账号", { exact: true }).click();
    await waitBridge("False", false);
    await input.fill("");
    await input.press("/");
    await expect(menu.getByRole("option")).toHaveCount(2);
    await expect(menu.getByText("私密测试")).toHaveCount(0);
    await input.press("Escape");
    console.log("PASS account visibility and keyboard navigation");

    await page.getByText("管理", { exact: true }).click();
    await page.getByRole("button", { name: "新建", exact: true }).click();
    await expect(page.getByRole("heading", { name: "新建快捷输入", exact: true })).toBeVisible();
    await page.getByRole("textbox", { name: "快捷输入名称", exact: true }).fill("浏览器测试");
    await page.getByRole("textbox", { name: "快捷输入名称", exact: true }).press("Tab");
    await page.getByRole("textbox", { name: "快捷输入内容", exact: true }).fill("第一行\n第二行");
    await page.getByRole("textbox", { name: "快捷输入内容", exact: true }).press("Tab");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByRole("radio", { name: "浏览器测试", exact: true })).toBeChecked();
    await page.getByText("聊天", { exact: true }).click();
    await input.fill("");
    await input.press("/");
    await page.keyboard.insertText("浏览器");
    await expect(menu.getByRole("option")).toHaveCount(1);
    await input.press("Enter");
    await expect(input).toHaveValue("第一行\n第二行");
    await page.screenshot({ path: "/tmp/dnd-quick-input-filled.png", fullPage: true });
    await input.press("Enter");
    await expect(page.getByText("发送次数： 1", { exact: true })).toBeVisible();
    console.log("PASS management save, multiline expansion and explicit send");
    await page.goto((process.env.TEST_URL || "http://localhost:8517") + "?full=1");
    await page.waitForLoadState("networkidle");
    await expect(input).toBeVisible();
    await page.waitForFunction(() => Boolean(window.__dndQuickInput && window.__dndEmptySpaceSubmitHandler));
    await input.press("/");
    await expect(menu).toBeVisible();
    const inputBox = await page.locator('.st-key-page2_chat_input [data-testid="stChatInput"]').boundingBox();
    const menuBox = await menu.boundingBox();
    assert.ok(menuBox.y >= inputBox.y + inputBox.height);
    assert.ok(menuBox.y + menuBox.height <= 900);
    await page.screenshot({ path: "/tmp/dnd-quick-input-menu.png", fullPage: true });
    await input.press("Enter");
    assert.ok((await input.inputValue()).startsWith("公开测试场景描述"));
    await expect(page.locator('[data-testid="stChatMessage"]')).toHaveCount(2);
    console.log("PASS actual chat layout, fully visible dropdown and no accidental send");
    assert.deepEqual(errors, []);
    console.log("All browser checks passed");
  } catch (error) {
    await page.screenshot({ path: "/tmp/dnd-quick-input-failure.png", fullPage: true });
    console.log("PAGE ERRORS", errors);
    console.log((await page.locator("body").innerText()).slice(0, 2000));
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
