"""Run against records_app.py to verify real click and form interactions."""
import os

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8549"))
            page.wait_for_load_state("networkidle")
            print("Controls:", page.get_by_role("button").all_text_contents())
            records = page.frame_locator('iframe[title="web.components.records_list.records_list"]')
            rows = records.get_by_role("radio")
            expect(rows).to_have_count(3)
            expect(page.get_by_role("textbox")).to_have_count(0)
            for label in ["打开", "重命名", "删除"]:
                expect(page.get_by_role("button", name=label, exact=True)).to_be_visible()
            button_box = page.get_by_role("button", name="打开", exact=True).bounding_box()
            assert abs(button_box["y"] - 60) <= 2, button_box

            # Double-click a previously unselected row; selection reruns must not
            # swallow the second click. Duplicate labels must still use IDs.
            rows.nth(2).dblclick(delay=120)
            expect(page.get_by_text("已打开：duplicate", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回记录").click()
            expect(rows).to_have_count(3)
            rows.nth(0).click()
            expect(rows.nth(0)).to_have_attribute("aria-checked", "true")
            page.get_by_role("button", name="重命名", exact=True).click()
            dialog = page.get_by_role("dialog")
            editor = dialog.get_by_role("textbox", name="记录名称")
            expect(editor).to_have_value("第一条记录")
            editor.fill("回车修改的名称")
            editor.press("Enter")
            expect(dialog).to_have_count(0)
            expect(rows.nth(0)).to_contain_text("回车修改的名称")

            rows.nth(1).click()
            expect(rows.nth(1)).to_have_attribute("aria-checked", "true")
            page.get_by_role("button", name="重命名", exact=True).click()
            expect(editor).to_have_value("第二条记录")
            editor.fill("确认修改的名称")
            dialog.get_by_role("button", name="确认", exact=True).click()
            expect(dialog).to_have_count(0)
            expect(rows.nth(1)).to_contain_text("确认修改的名称")
            expect(rows.nth(0)).to_contain_text("回车修改的名称")
            expect(page.get_by_role("textbox")).to_have_count(0)
            page.get_by_role("button", name="打开", exact=True).click()
            expect(page.get_by_text("已打开：two", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回记录").click()
            expect(rows).to_have_count(3)
            rows.nth(1).dblclick()
            expect(page.get_by_text("已打开：two", exact=True)).to_be_visible()
            page.get_by_role("button", name="返回记录").click()
            page.get_by_role("button", name="删除", exact=True).click()
            expect(dialog).to_contain_text("确认修改的名称")
            dialog.get_by_role("button", name="确认删除", exact=True).click()
            expect(rows).to_have_count(2)
            assert not errors, errors
            page.screenshot(path="/tmp/dnd-records-verified.png", full_page=True)
            print("PASS: double-click, duplicate names, Enter/confirm rename, selection, open, delete")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
