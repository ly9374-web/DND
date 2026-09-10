"""Run against tests/fixtures/video_prompt_app.py using an isolated data directory."""
import os

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **(
            {"executable_path": os.environ["CHROMIUM_EXECUTABLE"]}
            if os.environ.get("CHROMIUM_EXECUTABLE") else {}
        ))
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        base = os.environ.get("TEST_URL", "http://127.0.0.1:8521")

        def select_image(text):
            page.get_by_role("combobox").first.click()
            page.get_by_role("option").filter(has_text=text).click()

        def commit(text):
            editor.fill(text)
            page.get_by_role("heading", name="视频 prompt 交互验证").click()

        try:
            page.goto(base)
            page.wait_for_load_state("networkidle")
            editor = page.get_by_role("textbox", name="视频 prompt", exact=True)
            expect(editor).to_have_value("")
            expect(page.get_by_role("button", name="生视频prompt", exact=True)).to_be_visible()
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            expect(editor).to_have_value("蓝衣女子抬手微笑，转身看向身旁的人。故事8", timeout=15000)
            expect(page.get_by_role("button", name="生视频", exact=True)).to_be_visible()
            page.get_by_role("button", name="编辑第八轮", exact=True).click()
            commit(" \n ")
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            expect(editor).to_have_value("蓝衣女子抬手微笑，转身看向身旁的人。第八轮编辑后的故事", timeout=15000)
            print("PASS default blank, whitespace dispatch, current edited story, model fill")

            long_text = "蓝衣女子抬手微笑，走向房间另一侧。\n" * 40
            commit(long_text)
            expect(editor).to_have_value(long_text)
            geometry = editor.evaluate("e => ({height:e.getBoundingClientRect().height, scroll:e.scrollHeight, client:e.clientHeight})")
            assert 68 <= geometry["height"] <= 161, geometry
            assert geometry["scroll"] > geometry["client"], geometry
            editor.evaluate("e => e.scrollTop = e.scrollHeight")
            assert editor.evaluate("e => e.scrollTop") > 0
            page.screenshot(path="/tmp/dnd-video-prompt-editor.png", full_page=True)
            select_image("红衣女子")
            expect(editor).to_have_value("")
            commit("红衣女子挥手。")
            select_image("蓝衣女子")
            expect(editor).to_have_value(long_text)
            page.get_by_role("button", name="重新载入已保存图片").click()
            expect(editor).to_have_value(long_text)
            print("PASS height cap, internal scrolling, per-image drafts and reload")

            commit("")
            page.get_by_role("button", name="撤回末轮").click()
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            expect(editor).to_have_value("蓝衣女子抬手微笑，转身看向身旁的人。故事7", timeout=15000)
            commit("")
            page.get_by_role("button", name="撤回末轮").click()
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            expect(page.get_by_text("图片对应的原轮次已撤回，紧邻的上一轮也不存在，无法生成视频 prompt。", exact=True)).to_be_visible()
            expect(editor).to_have_value("")
            print("PASS exactly one-round fallback and double-undo error")

            select_image("缺少来源")
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            toast = page.locator(".st-key-page2_video_story_toast")
            expect(toast).to_be_visible()
            expect(toast.get_by_role("button", name="粘贴对应故事")).to_be_visible()
            assert toast.evaluate("e => getComputedStyle(e).position") == "fixed"
            page.screenshot(path="/tmp/dnd-video-prompt-toast.png", full_page=True)
            toast.get_by_role("button", name="粘贴对应故事").click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible()
            dialog.get_by_role("textbox").fill("旧图片对应的助手故事正文")
            dialog.get_by_role("button", name="保存故事").click()
            expect(dialog).not_to_be_visible()
            expect(toast).not_to_be_visible()
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            expect(editor).to_have_value("蓝衣女子抬手微笑，转身看向身旁的人。旧图片对应的助手故事正文", timeout=15000)
            page.get_by_role("button", name="重新载入已保存图片").click()
            expect(editor).to_have_value("蓝衣女子抬手微笑，转身看向身旁的人。旧图片对应的助手故事正文")
            print("PASS actionable toast, paste dialog, persisted manual story and generation")

            page.goto(base + "?fail=1")
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="生视频prompt", exact=True).click()
            expect(page.get_by_text("生视频prompt失败，队列将继续：模拟网络失败", exact=True)).to_be_visible(timeout=15000)
            expect(editor).to_have_value("")
            expect(page.get_by_role("button", name="生视频prompt", exact=True)).to_be_enabled()
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
            assert not errors, errors
            print("PASS failed requests preserve empty draft and allow retry; no browser exceptions")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
