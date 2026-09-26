"""Run against fixtures/independent_scroll_app.py with isolated application data."""
import os

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        left = page.locator(".st-key-page2_dialogue_pane")
        right = page.locator(".st-key-page2_media_pane")
        history = page.locator(".st-key-page2_chat_history")
        main = page.locator('section[data-testid="stMain"]')

        def top(locator):
            return locator.evaluate("e => e.scrollTop")

        def move_to(locator):
            box = locator.bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, max(30, min(700, box["y"] + box["height"] / 2)))

        def wheel(amount):
            page.mouse.wheel(0, amount)
            # End the wheel gesture before changing its target or scroll position.
            page.wait_for_timeout(350)

        try:
            page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8549"))
            page.wait_for_load_state("networkidle")
            page.get_by_role("heading", name="URL 收藏", exact=True).wait_for()
            page.wait_for_timeout(800)
            for pane in (left, right):
                assert pane.evaluate("e => e.clientHeight === 960 && e.scrollHeight > e.clientHeight")

            # Use a column margin to avoid nested inputs when testing the pane.
            box = right.bounding_box()
            page.mouse.move(box["x"] + 3, 450)
            wheel(400)
            assert top(right) > 0
            assert top(left) == top(history) == top(main) == 0
            right_position = top(right)
            move_to(history)
            wheel(400)
            assert top(history) > 0 and top(left) == 0
            assert top(right) == right_position and top(main) == 0
            print("PASS pointer location selects the pane without clicking")

            history.evaluate("e => e.scrollTop = e.scrollHeight")
            page.wait_for_timeout(350)
            move_to(history)
            wheel(420)
            assert top(left) > 0, "Chat boundary must allow access to Story Brain"
            assert top(right) == right_position and top(main) == 0
            left.evaluate("e => e.scrollTop = e.scrollHeight")
            expect(page.get_by_role("heading", name="Story Brain", exact=True)).to_be_in_viewport()
            box = left.bounding_box()
            page.mouse.move(box["x"] + 3, 450)
            wheel(1200)
            assert top(right) == right_position and top(main) == 0
            print("PASS chat hands scrolling to the left pane; Story Brain remains reachable")

            prompt = page.get_by_role("textbox", name="图片prompt（可编辑）", exact=True)
            prompt.scroll_into_view_if_needed()
            prompt.evaluate("e => e.scrollTop = 0")
            right_position, left_position = top(right), top(left)
            move_to(prompt)
            wheel(250)
            assert top(prompt) > 0 and top(right) == right_position
            assert top(left) == left_position and top(main) == 0
            prompt.evaluate("e => e.scrollTop = e.scrollHeight")
            page.wait_for_timeout(350)
            wheel(250)
            assert top(right) > right_position, "Text field should chain to its pane at the boundary"
            print("PASS long prompt scrolls internally before its containing pane")

            right.evaluate("e => e.scrollTop = e.scrollHeight")
            expect(page.get_by_role("heading", name="URL 收藏", exact=True)).to_be_in_viewport()
            box = right.bounding_box()
            page.mouse.move(box["x"] + 3, 450)
            wheel(2000)
            assert top(left) == left_position and top(main) == 0
            assert right.evaluate("e => getComputedStyle(e).scrollbarWidth") == "none"
            assert prompt.evaluate("e => getComputedStyle(e).scrollbarWidth") != "none"
            print("PASS right boundary stays contained; only the pane scrollbar is hidden")

            # A widget rerun and a completed chat must retain the other pane's position.
            left.evaluate("e => e.scrollTop = 0")
            history.evaluate("e => e.scrollTop = 1000")
            right_position = top(right)
            composer = page.locator('[data-testid="stChatInputTextArea"]')
            composer.fill("滚动位置验证")
            composer.press("Enter")
            expect(page.get_by_text("本地模拟回复", exact=True)).to_be_visible(timeout=15000)
            assert abs(top(right) - right_position) < 2
            assert top(left) == top(main) == 0
            page.screenshot(path="/tmp/dnd-independent-scroll-desktop.png")
            print("PASS chat submission and completion preserve right-pane position")

            # Narrow layouts stack only the two outer columns and restore page scrolling.
            for width in (700, 390):
                page.set_viewport_size({"width": width, "height": 850})
                page.wait_for_timeout(500)
                left_box, right_box = left.bounding_box(), right.bounding_box()
                assert abs(left_box["x"] - right_box["x"]) < 2
                assert right_box["y"] >= left_box["y"] + left_box["height"]
                assert right.evaluate("e => getComputedStyle(e).overflowY") == "visible"
                assert main.evaluate("e => getComputedStyle(e).overflowY") != "hidden"
                page.get_by_role("heading", name="URL 收藏", exact=True).scroll_into_view_if_needed()
                assert top(main) > 0
                page.screenshot(path=f"/tmp/dnd-independent-scroll-{width}.png")
            print("PASS narrow layouts stack and all content remains reachable")

            # Removing the workspace also removes its desktop scroll lock.
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_test_id("stExpandSidebarButton").click()
            page.get_by_role("button", name="切换测试页面", exact=True).click()
            expect(page.get_by_role("heading", name="其他页面", exact=True)).to_be_visible()
            expect(left).to_have_count(0)
            assert main.evaluate("e => getComputedStyle(e).overflowY") != "hidden"
            page.mouse.move(900, 450)
            wheel(600)
            assert top(main) > 0
            assert not errors, errors
            print("PASS other pages retain normal scrolling; no browser errors")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
