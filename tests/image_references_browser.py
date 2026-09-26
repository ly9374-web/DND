"""Exercise import, stable selection and saved-media identity through Streamlit."""
import base64
import json
import os

from playwright.sync_api import expect, sync_playwright

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8567"))
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("heading", name="图片引用验证")).to_be_visible()
            print("Rendered buttons:", page.get_by_role("button").all_text_contents())
            page.get_by_text("参考图片与本地素材", exact=True).click()
            expect(page.get_by_text("导入本地图片", exact=True)).to_be_visible()
            print("Rendered input types:", page.locator("input").evaluate_all("xs => xs.map(x => x.type)"))
            page.get_by_text("导入后永久保存", exact=True).click()
            expect(page.get_by_label("导入后永久保存", exact=True)).to_be_checked()
            page.locator('input[type="file"]').set_input_files({"name": "browser-upload.png", "mimeType": "image/png", "buffer": PNG})
            expect(page.get_by_role("button", name="添加到素材", exact=True)).to_have_count(0)
            expect(page.get_by_text("临时素材保留 48 小时；保存后可长期复用。", exact=True)).to_have_count(0)

            def state():
                return json.loads(page.locator('[data-testid="stCode"]').last.inner_text())

            page.wait_for_function('JSON.parse([...document.querySelectorAll(\'[data-testid="stCode"]\')].at(-1).innerText).images.length === 2')
            value = state()
            assert len(value["images"]) == 2
            imported = value["images"][1]
            assert imported["saved"] and imported["base64"] is None
            assert value["references"] == ["media://" + imported["id"]]
            assert value["preview"] == value["video"] == imported["id"]
            expect(page.locator('[data-testid="stImage"] img')).to_have_count(1)
            page.get_by_role("button", name="固定本次任务的参考图", exact=True).click()
            expect(page.locator('[data-testid="stCode"]').last).to_contain_text('"frozen": ["media://')
            # Editing the draft cannot mutate references already frozen for a task.
            page.get_by_role("textbox", name="参考图片 URL（每行一个，可选）").fill("https://example.com/extra.png")
            page.get_by_role("heading", name="图片引用验证").click()
            expect(page.locator('[data-testid="stCode"]').last).to_contain_text("extra.png")
            assert state()["frozen"] == ["media://" + imported["id"]]
            first_id = value["images"][0]["id"]
            page.get_by_role("button", name="保存第一张图片", exact=True).click()
            page.wait_for_function('JSON.parse([...document.querySelectorAll(\'[data-testid="stCode"]\')].at(-1).innerText).images[0].saved === true')
            current = state()
            assert current["images"][0]["id"] == first_id
            assert current["images"][0]["saved"]
            # Select a library image from another chat: it becomes the preview/video
            # input while the previously imported image remains an image reference.
            library_id = next(item["id"] for item in current["library"] if item["name"] == "其他聊天的永久图")
            print("Multiselect inputs:", page.get_by_test_id("stMultiSelect").locator("input").evaluate_all(
                "xs => xs.map(x => ({role: x.getAttribute('role'), label: x.getAttribute('aria-label')}))"))
            picker = page.get_by_test_id("stMultiSelect").locator("input")
            if not picker.is_visible():
                page.get_by_text("参考图片与本地素材", exact=True).click()
            expect(picker).to_be_visible()
            picker.fill("其他聊天的永久图")
            option = page.get_by_role("option").filter(has_text="其他聊天的永久图").filter(has_text=library_id[:8])
            expect(option).to_be_visible()
            option.click()
            picker.press("Escape")
            page.wait_for_function('(id) => JSON.parse([...document.querySelectorAll(\'[data-testid="stCode"]\')].at(-1).innerText).video === id', arg=library_id)
            value = state()
            assert value["preview"] == library_id
            assert value["references"][:2] == ["media://" + imported["id"], "media://" + library_id]
            assert len(value["images"]) == 2  # no duplicate import on unrelated reruns
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
            page.screenshot(path=os.environ.get("SCREENSHOT", "/tmp/dnd-image-references.png"), full_page=True)
            assert not errors, errors
            print("PASS: automatic import, permanent storage, global library, preview/video sync, multiple references, no duplicate import, no network")
        except Exception:
            page.screenshot(path="/tmp/dnd-reference-browser-failure.png", full_page=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    run()
