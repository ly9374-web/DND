"""Verify the chat-log control and removal of the global Cloudinary switch."""
import os

from playwright.sync_api import expect, sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8568"))
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="APIkey", exact=True)).to_be_visible()
    expect(page.get_by_text("图片兼容上传（Cloudinary）", exact=True)).to_have_count(0)
    debug = page.get_by_role("checkbox", name="打印并保存聊天日志（48 小时）")
    debug_label = page.get_by_text("打印并保存聊天日志（48 小时）", exact=True)
    expect(debug_label).to_be_visible()
    debug_label.click()
    expect(debug).to_be_checked()
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)
    page.screenshot(path=os.environ.get("SCREENSHOT", "/tmp/dnd-model-settings.png"), full_page=True)
    browser.close()
    print("PASS: Cloudinary compatibility switch removed; terminal/file chat log control is present")
