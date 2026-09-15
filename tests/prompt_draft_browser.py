"""Run against tests/fixtures/prompt_draft_app.py with isolated server data."""
from __future__ import annotations

import os
from uuid import uuid4
from playwright.sync_api import expect, sync_playwright

TITLE = "修改尚未保存，是否离开？"


def ready(page):
    page.wait_for_function("Boolean(window.__dndPromptDraftGuard)")
    page.wait_for_timeout(500)
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)


def quiet(page):
    ready(page)
    expect(page.get_by_role("dialog", name=TITLE)).to_have_count(0)


def section(page, name):
    page.get_by_role("button", name=name, exact=True).click()
    ready(page)


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        errors, native_dialogs = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: (native_dialogs.append(dialog.type), dialog.accept()))
        try:
            page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8533"))
            page.wait_for_load_state("networkidle")
            ready(page)
            editor = page.get_by_role("textbox", name="prompt", exact=True)
            baseline = editor.input_value()
            draft = baseline + "\n导航草稿测试"

            editor.fill(draft)
            # Current section and an ordinary dropdown must never prompt.
            section(page, "最终设定")
            quiet(page)
            page.get_by_role("combobox").first.click()
            page.keyboard.press("Escape")
            quiet(page)
            page.get_by_role("button", name="世界书", exact=True).focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("dialog", name=TITLE)).to_be_visible()
            page.get_by_role("button", name="继续编辑", exact=True).click()
            expect(editor).to_have_value(draft)
            section(page, "世界书")
            page.get_by_role("button", name="放弃修改并离开", exact=True).click()
            expect(page.get_by_role("textbox", name="世界书", exact=True)).to_be_visible(timeout=15000)
            section(page, "最终设定")
            expect(editor).to_have_value(baseline)
            quiet(page)
            print("PASS ordinary actions/current section stay quiet; navigation/keep/discard", flush=True)

            saved = baseline + "\n保存离开测试"
            editor.fill(saved + "\n")
            section(page, "世界书")
            page.get_by_role("button", name="保存并离开", exact=True).click()
            expect(page.get_by_role("textbox", name="世界书", exact=True)).to_be_visible(timeout=15000)
            section(page, "最终设定")
            expect(editor).to_have_value(saved)
            section(page, "世界书")
            quiet(page)
            section(page, "最终设定")
            # Re-edit after saving: compare with the NEW persisted baseline.
            editor.fill(baseline)
            section(page, "世界书")
            expect(page.get_by_role("dialog", name=TITLE)).to_be_visible()
            page.get_by_role("button", name="继续编辑", exact=True).click()
            page.get_by_role("button", name="保存", exact=True).click()
            ready(page)
            section(page, "世界书")
            quiet(page)
            print("PASS save-and-leave acknowledgement, whitespace normalization, re-edit, explicit save", flush=True)

            # Same-page record switches keep drafts quietly; new records have new widget IDs.
            section(page, "快捷输入")
            page.get_by_role("button", name="新建", exact=True).click()
            ready(page)
            title = page.get_by_role("textbox", name="快捷输入名称", exact=True)
            content = page.get_by_role("textbox", name="快捷输入内容", exact=True)
            new_title = "导航测试新记录" + uuid4().hex[:8]
            title.fill(new_title)
            content.fill("新建记录的正文")
            section(page, "世界书")
            page.get_by_role("button", name="保存并离开", exact=True).click()
            expect(page.get_by_role("textbox", name="世界书", exact=True)).to_be_visible(timeout=15000)
            section(page, "快捷输入")
            page.locator('[data-testid="stRadio"] label[data-baseweb="radio"]').filter(has_text=new_title).click()
            ready(page)
            expect(content).to_have_value("新建记录的正文")
            content.fill("同页切换草稿")
            choices = page.locator('[data-testid="stRadio"] label[data-baseweb="radio"]')
            choices.filter(has_not_text=new_title).first.click()
            quiet(page)
            choices.filter(has_text=new_title).click()
            expect(content).to_have_value("同页切换草稿")
            quiet(page)
            # Backend validation failure must keep the user on the editor with its error.
            content.fill("")
            section(page, "世界书")
            page.get_by_role("button", name="保存并离开", exact=True).click()
            expect(page.get_by_role("textbox", name="快捷输入内容", exact=True)).to_be_visible()
            expect(page.get_by_text("请填写快捷输入内容。", exact=True)).to_be_visible(timeout=10000)
            quiet(page)
            content.fill("修正后的正文")
            page.get_by_role("button", name="保存", exact=True).click()
            ready(page)
            section(page, "最终设定")
            quiet(page)
            print("PASS new-record save, same-page record switching/draft restoration and failed-save recovery", flush=True)

            # Each real editor must dispatch to its own save handler.
            for name, label in [("世界书", "世界书"), ("角色卡", "角色卡"),
                                ("固定模板", "DM 的核心任务"), ("长期记忆", "长期记忆")]:
                section(page, name)
                field = page.get_by_role("textbox", name=label, exact=True)
                previous = field.input_value()
                modified = previous + "\n对应编辑器保存测试"
                field.fill(modified)
                section(page, "最终设定")
                page.get_by_role("button", name="保存并离开", exact=True).click()
                expect(editor).to_be_visible(timeout=15000)
                section(page, name)
                expect(field).to_have_value(modified.strip())
                field.fill(previous)
                page.get_by_role("button", name="保存", exact=True).click()
                ready(page)
                section(page, "最终设定")
                quiet(page)
            print("PASS world book, character card, fixed template and long-term memory save handlers", flush=True)

            recovery = editor.input_value() + "\n刷新静默草稿"
            editor.fill(recovery)
            page.reload(wait_until="networkidle")
            ready(page)
            expect(editor).to_have_value(recovery, timeout=15000)
            quiet(page)
            assert native_dialogs == [], native_dialogs
            # Other tab writes must not cause a conflict modal or overwrite active editing.
            other = context.new_page()
            other.goto(page.url)
            other.wait_for_load_state("networkidle")
            ready(other)
            other.get_by_role("textbox", name="prompt", exact=True).fill(recovery + "另一个标签页")
            quiet(page)
            expect(editor).to_have_value(recovery)
            other.close()
            page.get_by_role("button", name="返回", exact=True).click()
            expect(page.get_by_role("dialog", name=TITLE)).to_be_visible()
            page.get_by_role("button", name="放弃修改并离开", exact=True).click()
            expect(page.get_by_role("heading", name="测试首页")).to_be_visible(timeout=15000)
            page.get_by_role("button", name="Prompt", exact=True).click()
            ready(page)
            quiet(page)
            assert "刷新静默草稿" not in editor.input_value()
            print("PASS refresh/cross-tab silence, sidebar navigation, discarded draft never resurrects", flush=True)

            page.get_by_role("button", name="返回", exact=True).click()
            page.get_by_role("button", name="自动保存页面", exact=True).click()
            ready(page)
            auto = page.get_by_role("textbox", name="自动保存 prompt", exact=True)
            auto.fill("自动保存修改")
            page.get_by_role("button", name="普通操作", exact=True).click()
            quiet(page)
            auto.fill("直接离开的自动保存修改")
            page.get_by_role("button", name="返回", exact=True).click()
            expect(page.get_by_role("heading", name="测试首页")).to_be_visible(timeout=15000)
            quiet(page)
            assert not errors, errors
            print("PASS autosave fields never block ordinary actions or navigation; no JS errors", flush=True)
        except Exception:
            page.screenshot(path="/tmp/dnd-prompt-draft-failure.png", full_page=True)
            print(page.locator("body").inner_text()[-3000:])
            print("STATE", page.evaluate("() => ({drafts: [...(window.__dndPromptDraftState?.drafts || [])].map(([k,v])=>[k,{...v,value:String(v.value).slice(-80),baseline:String(v.baseline).slice(-80)}]),pending: window.__dndPromptDraftState?.pending})"))
            print("ERRORS", errors)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    run()
