const { chromium } = require(process.env.PLAYWRIGHT_NODE_MODULE || "playwright/test");
console.log("loaded playwright");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-proxy-server", "--proxy-bypass-list=*"],
    proxy: { server: "direct://" },
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}),
  });
  console.log("launched");
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:8517/?full=1");
  await page.waitForLoadState("networkidle");
  console.log("title:", await page.title());
  const has = await page.evaluate(() => Boolean(window.__dndQuickInput));
  console.log("bridge:", has);
  console.log("html:", (await page.locator("body").innerText()).slice(0, 300));
  await browser.close();
})().catch((error) => { console.error("FAIL", error); process.exitCode = 1; });