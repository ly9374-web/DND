"""Deferred tools and native Story Brain editor acceptance."""
import os
from playwright.sync_api import sync_playwright, expect


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1512, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8567"))
        page.wait_for_load_state("networkidle")
        page.wait_for_function("document.body.classList.contains('dnd-game-ready')")
        expect(page.locator('[data-testid=stChatMessage]')).to_have_count(12)
        assert page.get_by_role("combobox", name="聊天模型", exact=True).count() == 0
        assert page.get_by_role("combobox", name="选择记录", exact=True).count() == 0
        assert page.locator('.st-key-page2_brain_editor textarea').count() == 0
        assert page.locator('.st-key-page2_brain_scroll iframe').count() == 0
        composer = page.locator('.st-key-page2_chat_composer')
        composer.hover()
        page.mouse.wheel(0, 100)
        page.wait_for_timeout(250)
        viewer = page.locator('.dnd-brain-viewer')
        viewer.dblclick(position={"x": 80, "y": 20})
        editor = page.locator('.st-key-page2_brain_editor textarea')
        expect(editor).to_be_visible()
        # The editor fills the panel instead of overflowing it, and the actions
        # share one row so there is room to actually write.
        page.wait_for_timeout(400)
        panel = page.locator('.st-key-page2_story_brain_panel').bounding_box()
        box = editor.bounding_box()
        save = page.locator('.st-key-page2_brain_save').bounding_box()
        cancel = page.locator('.st-key-page2_brain_cancel').bounding_box()
        assert box['height'] > 300, box
        assert box['y'] >= panel['y'] and box['y'] + box['height'] <= panel['y'] + panel['height'], (panel, box)
        assert abs(save['y'] - cancel['y']) < 2 and cancel['x'] > save['x'] + 100, (save, cancel)
        editor.fill("已验证的 Story Brain\n第二行记忆")
        editor.press("Control+Enter")
        expect(viewer).to_contain_text("第二行记忆")
        assert editor.count() == 0
        viewer.dblclick(position={"x": 80, "y": 20})
        expect(editor).to_be_visible()
        expect(editor).to_have_value("已验证的 Story Brain\n第二行记忆")
        editor.fill("这段修改应该被取消")
        editor.press("Escape")
        expect(viewer).to_contain_text("第二行记忆")
        viewer.dblclick(position={"x": 80, "y": 20})
        expect(editor).to_have_value("已验证的 Story Brain\n第二行记忆")
        page.locator('.st-key-page2_brain_cancel button').click()
        expect(viewer).to_be_visible()
        assert not page.locator('[data-testid=stException]').count()
        assert not errors, errors
        browser.close()
        print("PASS deferred controls, no reading iframe, native editor save/reopen/cancel and shortcuts")


if __name__ == "__main__":
    run()
