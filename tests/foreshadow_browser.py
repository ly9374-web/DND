"""Run against fixtures/foreshadow_app.py; all model calls are deterministic."""
from playwright.sync_api import expect, sync_playwright


def healthy(page):
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)


def answer_questions(page):
    dialog = page.get_by_role("dialog")
    for i in range(1, 9):
        expect(dialog.get_by_text(f"第{i}项世界发展方向？")).to_be_visible()
    box = dialog.get_by_role("textbox", name="你的回答", exact=True)
    box.fill("1 A\n2 B\n3 C\n4 D\n5 A\n6 B\n7 C\n8 D")
    return dialog


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 1100})
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto("http://127.0.0.1:8566")
    page.wait_for_load_state("networkidle")
    generate = page.get_by_role("button", name="生成事件", exact=True)
    expect(generate).to_be_visible(timeout=20000)
    healthy(page)
    expander = page.locator('[data-testid="stExpander"]').filter(has=page.get_by_text("事件", exact=True))
    expect(expander.locator("details")).not_to_have_attribute("open", "")
    expect(page.get_by_text("生成故事草案", exact=True)).to_have_count(0)
    expect(page.get_by_text("生成考题", exact=True)).to_have_count(0)
    generate.click()
    dialog = answer_questions(page)
    dialog.get_by_role("button", name="生成背景与事件", exact=True).click()
    dialog = page.get_by_role("dialog")
    draft = dialog.get_by_role("textbox", name="故事背景", exact=True)
    expect(draft).to_be_visible(timeout=15000)
    expect(dialog.get_by_text("场景事件 · 5条", exact=True)).to_be_visible()
    expect(dialog.get_by_text("时间事件 · 5条", exact=True)).to_be_visible()
    expect(page.get_by_text("模型调用记录：问卷 → 背景与事件", exact=True)).to_have_count(1)
    draft.fill("手动确认的幕后故事：第1天A回到宿舍。")
    dialog.get_by_role("button", name="确定", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0, timeout=15000)
    expander.get_by_text("事件", exact=True).click()
    expect(page.get_by_role("textbox", name="故事背景", exact=True)).to_have_value("手动确认的幕后故事：第1天A回到宿舍。")
    expect(page.get_by_text("模型调用记录：问卷 → 背景与事件", exact=True)).to_have_count(1)
    expect(page.get_by_text("场景事件 · 5条", exact=True)).to_be_visible()
    expect(page.get_by_text("时间事件 · 5条", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="事件 user prompt 内容", exact=True).first.fill("手动修改的隐藏事件")
    page.get_by_text("启用此条", exact=True).first.click()
    page.get_by_role("button", name="保存事件", exact=True).click()
    healthy(page)
    expect(page.get_by_role("textbox", name="事件 user prompt 内容", exact=True).first).to_have_value("手动修改的隐藏事件")
    expect(page.get_by_role("checkbox", name="启用此条", exact=True).first).not_to_be_checked()
    page.get_by_role("button", name="增加场景事件", exact=True).click()
    expect(page.get_by_text("场景事件 · 6条", exact=True)).to_be_visible()
    page.get_by_role("button", name="删除此条", exact=True).nth(5).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("场景事件 6 · 待设置地点")
    dialog.get_by_role("button", name="确认删除", exact=True).click()
    expect(page.get_by_text("场景事件 · 5条", exact=True)).to_be_visible()
    expect(page.get_by_text("时间事件 · 5条", exact=True)).to_be_visible()
    page.screenshot(path="/tmp/dnd-foreshadow-editor.png", full_page=True)
    print("PASS exactly two model calls; combined background + 5+5 events; edits, disable, single delete", flush=True)

    # Cancellation preserves the existing plan.
    generate.click()
    page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("textbox", name="事件 user prompt 内容", exact=True).first).to_have_value("手动修改的隐藏事件")

    # Re-generation repeats only the combined second round and preserves answers.
    generate.click()
    dialog = answer_questions(page)
    dialog.get_by_role("button", name="生成背景与事件", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("textbox", name="故事背景", exact=True)).to_be_visible(timeout=15000)
    dialog.get_by_role("textbox", name="故事背景", exact=True).fill("准备重新生成")
    dialog.get_by_role("button", name="重新生成", exact=True).click()
    expect(dialog.get_by_role("textbox", name="故事背景", exact=True)).to_have_value("第1天14:00，A回到学校403宿舍。第2天，收粮队抵达。地下室信件与收粮队有关。")
    expect(page.get_by_text("模型调用记录：问卷 → 背景与事件 → 问卷 → 问卷 → 背景与事件 → 背景与事件", exact=True)).to_have_count(1)
    dialog.get_by_role("button", name="取消", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("textbox", name="方案名称", exact=True)).to_have_value("事件方案 1")
    expect(page.get_by_role("textbox", name="事件 user prompt 内容", exact=True).first).to_have_value("手动修改的隐藏事件")
    print("PASS regenerate only second round; cancel preserves original plan", flush=True)

    # Incomplete combined JSON leaves the existing plan untouched and can be cancelled.
    generate.click()
    dialog = answer_questions(page)
    dialog.get_by_role("textbox", name="你的回答", exact=True).fill("测试生成失败")
    dialog.get_by_role("button", name="生成背景与事件", exact=True).click()
    expect(dialog.get_by_text("模型没有返回故事背景，请重新生成；原方案未被修改。", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="取消", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("textbox", name="方案名称", exact=True)).to_have_value("事件方案 1")
    healthy(page)
    assert not errors, errors
    print("PASS malformed output preserves saved plan; no browser errors", flush=True)
    browser.close()
