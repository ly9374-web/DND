"""Run against concurrency_app.py, with separate data and mocked model calls."""
import os

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8537"))
            page.wait_for_load_state("networkidle")
            print("Rendered controls:", page.get_by_role("button").all_text_contents())
            expect(page.get_by_text("验证预览选择：old-image", exact=True)).to_be_visible()
            page.get_by_role("button", name="生视频", exact=True).click()
            expect(page.get_by_text("视频：正在执行生视频", exact=True)).to_be_visible()
            page.get_by_role("button", name="生成图片", exact=True).click()
            expect(page.get_by_text("图片：正在执行生成图片", exact=True)).to_be_visible()

            composer = page.locator('[data-testid="stChatInputTextArea"]')
            composer.fill("第一条并发聊天")
            composer.press("Enter")
            expect(page.get_by_text("回复：第一条并发聊天；已有 1 轮", exact=True)).to_be_visible(timeout=15000)
            composer.fill("第二条并发聊天")
            composer.press("Enter")
            expect(page.get_by_text("回复：第二条并发聊天；已有 2 轮", exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_text("视频：正在执行生视频", exact=True)).to_be_visible()
            expect(page.get_by_text("图片：正在执行生成图片", exact=True)).to_be_visible()
            print("PASS chat advances in order while both media requests remain running")

            image_prompt = page.get_by_role("textbox", name="图片prompt（可编辑）", exact=True)
            video_prompt = page.get_by_role("textbox", name="视频 prompt", exact=True)
            image_prompt.fill("我新写的图片草稿")
            page.get_by_role("heading", name="并发交互验证").click()
            video_prompt.fill("我新写的视频草稿")
            page.get_by_role("heading", name="并发交互验证").click()
            composer.fill("尚未发送的聊天草稿")
            page.get_by_role("button", name="完成模拟视频", exact=True).click()
            expect(page.get_by_text("视频生成完成", exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_text("验证预览选择：old-image", exact=True)).to_be_visible()
            page.get_by_role("button", name="完成模拟图片", exact=True).click()
            expect(page.get_by_text("验证预览选择：generated-image", exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_text("验证视频输入：old-image", exact=True)).to_be_visible()
            expect(image_prompt).to_have_value("我新写的图片草稿")
            expect(video_prompt).to_have_value("我新写的视频草稿")
            expect(composer).to_have_value("尚未发送的聊天草稿")
            expect(page.get_by_role("button", name="查看", exact=True)).to_have_count(1)
            print("PASS images display automatically; video input and all drafts remain intact")

            # Only the video requires an explicit View action.
            page.get_by_role("button", name="查看", exact=True).click()
            expect(page.get_by_text("验证预览选择：generated-video", exact=True)).to_be_visible()
            expect(page.get_by_text("视频生成完成", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="加载视频播放", exact=True)).to_be_visible()
            expect(video_prompt).to_have_value("我新写的视频草稿")
            expect(page.get_by_text("验证视频输入：old-image", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="查看", exact=True)).to_have_count(0)
            expect(page.get_by_text("回复：第二条并发聊天；已有 2 轮", exact=True)).to_be_visible()
            assert not errors, errors
            page.screenshot(path="/tmp/dnd-concurrency-verified.png", full_page=True)
            print("PASS View targets exact results; chat survives completion and selection changes")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
