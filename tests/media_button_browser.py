"""Browser regression for immediate click guards and media queue locks."""
import os

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8543"))
            page.wait_for_load_state("networkidle")
            print("Rendered buttons:", page.get_by_role("button").all_text_contents(), flush=True)
            page.wait_for_function("!!window.__dndMediaButtonGuard")

            def button(name):
                return page.get_by_role("button", name=name, exact=True)

            def rapid_click(target):
                expect(target).to_be_enabled()
                target.evaluate("b => { b.click(); b.click(); }")

            def finish():
                button("完成当前任务").click()

            rapid_click(button("生成图片prompt"))
            expect(button("生成图片prompt")).to_have_attribute("disabled", "")
            expect(button("生成图片")).to_be_enabled()
            rapid_click(button("生成图片"))
            expect(button("生成图片")).to_have_attribute("disabled", "")
            rapid_click(button("生视频prompt"))
            expect(button("生视频prompt")).to_have_attribute("disabled", "")
            page.get_by_role("textbox", name="视频 prompt", exact=True).fill("manual motion")
            page.get_by_role("heading", name="图生视频", exact=True).click()
            expect(button("生视频")).to_be_disabled()
            print("Queued before completion:", page.locator('[data-testid="stCode"]').last.inner_text(), flush=True)
            # Both first-stage requests finish; the queued video prompt then starts.
            finish()
            expect(button("生成图片prompt")).to_be_enabled()
            expect(button("生成图片")).to_be_enabled()
            expect(button("生视频")).to_be_disabled()
            finish()
            expect(button("生视频")).to_be_enabled()
            rapid_click(button("生视频"))
            expect(button("生视频")).to_have_attribute("disabled", "")
            expect(page.get_by_text("视频：正在执行生视频", exact=True)).to_be_visible()
            button("当前任务失败").click()
            expect(button("生视频")).to_be_enabled()
            print("PASS generation buttons lock independently across queueing, label changes and failure", flush=True)

            page.get_by_role("textbox", name="视频 prompt", exact=True).fill("")
            page.get_by_role("heading", name="图生视频", exact=True).click()
            rapid_click(button("一键生成"))
            expect(button("一键生成")).to_be_disabled()
            for stage in ("1/3 · 图片 prompt", "2/3 · 图片", "3/3 · 视频 prompt"):
                expect(page.get_by_text("正在执行一键生成 " + stage, exact=False)).to_be_visible()
                finish()
            expect(button("生视频")).to_be_enabled()
            expect(button("一键生成")).to_be_disabled()
            expect(page.get_by_role("textbox", name="视频 prompt", exact=True)).to_have_value("generated video prompt")
            print("PASS one-click completes three steps, unlocks and stops before video", flush=True)

            rapid_click(button("保存"))
            expect(page.locator('[data-testid="stCode"]').last).to_contain_text('"saved": 1')
            expect(button("保存")).to_be_enabled()
            with page.expect_download():
                rapid_click(button("下载"))
            expect(button("下载")).to_be_disabled()
            expect(button("下载")).to_be_enabled(timeout=5000)
            # Locate the actual copy iframe among the sizing and guard helpers.
            frame = next(frame for frame in page.frames if frame.get_by_role("button", name="复制", exact=True).count())
            copy = frame.get_by_role("button", name="复制", exact=True)
            rapid_click(copy)
            expect(frame.get_by_role("button")).to_be_disabled()
            expect(copy).to_be_enabled(timeout=5000)
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
            assert not errors, errors
            page.screenshot(path="/private/tmp/dnd-media-button-locks.png", full_page=True)
            print("PASS save/download/copy resist double clicks and recover; no browser exceptions", flush=True)
        except Exception:
            print("GUARD", page.evaluate("({token: window.__dndMediaButtonGuard?.token, frames: [...document.querySelectorAll('iframe')].map(f=>({src:f.src, srcdoc:f.srcdoc?.slice(-180)}))})"), flush=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    run()
