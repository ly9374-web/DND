// Temporary diagnostic: select N characters inside the chat message and read the composer.
const { chromium } = require(process.env.PLAYWRIGHT_NODE_MODULE || "playwright/test");

const url = process.env.TEST_URL || "http://127.0.0.1:8517/?full=1";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.on("pageerror", (error) => console.log("PAGEERROR", error.message));
  await page.goto(url);
  await page.waitForLoadState("networkidle");
  await page.waitForFunction(() => Boolean(window.__dndQuickInput));
  await page.waitForSelector('.st-key-page2_chat_history [data-testid="stChatMessageContent"]');
  await page.waitForTimeout(600);

  const message = page.locator('.st-key-page2_chat_history [data-testid="stChatMessageContent"]').last();
  const input = page.locator(".st-key-page2_chat_input textarea");

  const rects = await message.evaluate((el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const out = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const text = node.textContent || "";
      for (let index = 0; index < text.length; index += 1) {
        if (!text[index].trim()) continue;
        const range = document.createRange();
        range.setStart(node, index);
        range.setEnd(node, index + 1);
        const box = range.getBoundingClientRect();
        if (box.width === 0) continue;
        out.push({ char: text[index], x: box.x, y: box.y, w: box.width, h: box.height });
      }
    }
    return out;
  });
  console.log("chars:", rects.slice(0, 12).map((r) => r.char).join(""));

  async function clearComposer() {
    await input.evaluate((el) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(el, "");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForTimeout(250);
  }

  async function dragSelect(count) {
    const first = rects[0];
    const last = rects[count - 1];
    if (!first || !last) return null;
    const y = first.y + first.h / 2;
    await page.mouse.move(first.x + 1, y);
    await page.mouse.down();
    await page.mouse.move(first.x + first.w / 2, y, { steps: 2 });
    await page.mouse.move(last.x + last.w - 1, y, { steps: 4 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    return input.inputValue();
  }

  for (const count of [1, 2, 3, 4]) {
    await clearComposer();
    const value = await dragSelect(count);
    const selected = await page.evaluate(() => window.getSelection().toString());
    console.log(`drag ${count} char(s) -> selection=${JSON.stringify(selected)} composer=${JSON.stringify(value)}`);
  }

  await clearComposer();
  await message.evaluate((el) => {
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 1);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await page.waitForTimeout(100);
  const viaShift = await page.evaluate(async () => {
    document.dispatchEvent(new KeyboardEvent("keyup", { key: "Shift", bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 300));
    return document.querySelector(".st-key-page2_chat_input textarea").value;
  });
  console.log(`programmatic 1-char + Shift keyup -> composer=${JSON.stringify(viaShift)}`);

  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});