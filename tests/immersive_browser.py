"""Browser acceptance for the real Page2 immersive shell. Run with immersive_app.py."""
import os
from playwright.sync_api import sync_playwright, expect


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1512, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8558"))
        page.locator("#dnd-chat-resize").wait_for()
        page.wait_for_function("document.querySelector('#dnd-immersive-bg img').naturalWidth>0")

        def check_drawer_motion(selector, button):
            page.evaluate("""selector=>{
                window.drawerMotion={done:false,samples:[]};
                const start=performance.now();
                function sample(){
                    const panel=document.querySelector(selector),chat=document.querySelector('.st-key-page2_chat_history');
                    drawerMotion.samples.push([panel.getBoundingClientRect().x,chat.getBoundingClientRect().x,chat.getBoundingClientRect().width]);
                    if(performance.now()-start<900)requestAnimationFrame(sample);else drawerMotion.done=true;
                }sample();
            }""", selector)
            page.get_by_role("button", name=button, exact=True).click()
            page.wait_for_function("window.drawerMotion.done")
            samples=page.evaluate("window.drawerMotion.samples")
            for index in (0, 1 if button == "游戏设置" else 2):
                positions=[sample[index] for sample in samples]
                low,high=min(positions),max(positions)
                assert high-low>100, (button, index, low, high)
                assert any(low+5<x<high-5 for x in positions), (button, "jumped instead of animating", samples)

        # Both open and close must visibly pass through intermediate positions;
        # the reading surface follows the same motion rather than jumping.
        for button,selector in (("游戏设置", '[data-testid="stSidebar"]'),("图片与视频", ".st-key-page2_media_pane")):
            check_drawer_motion(selector, button)
            check_drawer_motion(selector, button)
        h = page.locator(".st-key-page2_chat_history")
        composer = page.locator(".st-key-page2_chat_composer")
        expect(h).to_have_css("height", "450px")
        bottom = composer.bounding_box()["y"]
        assert abs(bottom + composer.bounding_box()["height"] - 894) < 1
        assert page.locator(".st-key-page2_chat_input [data-testid=stChatInputSubmitButton]").evaluate("e=>getComputedStyle(e).visibility") == "hidden"
        # Resize is browser-local and leaves the composer pinned.
        handle = page.locator("#dnd-chat-resize").bounding_box()
        page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + 10)
        page.mouse.down()
        page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] - 60, steps=8)
        page.mouse.up()
        assert abs(h.bounding_box()["height"] - 520) < 1
        assert abs(composer.bounding_box()["y"] - bottom) < 1
        # Native sidebars contain the actual controls and reserve center space.
        page.get_by_role("button", name="游戏设置", exact=True).click()
        page.get_by_role("button", name="图片与视频", exact=True).click()
        expect(h).to_have_css("width", "814px")
        expect(page.get_by_role("combobox", name="聊天模型", exact=True)).to_be_visible()
        page.locator(".st-key-page2_veil_opacity").scroll_into_view_if_needed()
        slider = page.locator(".st-key-page2_veil_opacity input[type=range]")
        track = page.locator('.st-key-page2_veil_opacity [data-rac][role=group] > [data-rac]').bounding_box()
        y=track["y"]+track["height"]/2
        page.mouse.move(track["x"]+track["width"]*int(slider.input_value())/98,y)
        page.mouse.down()
        gradients=[]
        for fraction in (.2,.8):
            page.mouse.move(track["x"]+track["width"]*fraction,y,steps=5)
            page.wait_for_function("""()=>Math.abs(parseFloat(document.body.style.getPropertyValue('--dnd-veil'))-
                Number(document.querySelector('.st-key-page2_veil_opacity input').value)/100)<.001""")
            assert abs(int(slider.input_value())-round(98*fraction))<=1
            gradients.append(page.locator('#dnd-immersive-veil').evaluate("e=>getComputedStyle(e).backgroundImage"))
        assert gradients[0]!=gradients[1], "Veil must change before releasing the mouse"
        page.mouse.up()
        page.wait_for_timeout(700)
        assert page.locator('#dnd-immersive-veil').evaluate("e=>getComputedStyle(e).backgroundImage")==gradients[-1]
        slider.focus()
        slider.press("ArrowLeft")
        page.wait_for_timeout(700)
        assert abs(h.bounding_box()["height"] - 520) < 1
        page.get_by_role("button", name="游戏设置", exact=True).click()
        # Explicit empty background remains blank across native widget reruns.
        picker = page.get_by_role("combobox", name="选择记录", exact=True)
        picker.scroll_into_view_if_needed()
        picker.click()
        page.get_by_role("option", name="无图片／正常背景", exact=True).click()
        page.wait_for_function("!document.body.classList.contains('dnd-has-media')")
        assert abs(composer.bounding_box()["y"] - bottom) < 1
        picker.click()
        page.get_by_role("option", name="IMAGE • grok", exact=False).first.click()
        page.wait_for_function("document.body.classList.contains('dnd-has-media')")
        page.get_by_role("button", name="图片与视频", exact=True).click()
        page.wait_for_timeout(450)
        # Native surfaces scroll independently; only the composer toggles Brain.
        assert page.locator('[data-testid=stChatMessage]').count() == 12
        h.evaluate("e=>e.scrollTop=e.scrollHeight")
        h.hover(position={"x":150,"y":100})
        page.mouse.wheel(0,120)
        page.wait_for_timeout(150)
        assert page.evaluate("__dndImmersive.getState().reveal") == 0
        composer.hover()
        page.mouse.wheel(0,100)
        page.wait_for_function("__dndImmersive.getState().reveal===300")
        page.wait_for_timeout(250)
        brain=page.locator('.st-key-page2_brain_scroll')
        h.evaluate('e=>e.scrollTop=500')
        brain.evaluate('e=>e.scrollTop=100')
        h.hover(position={"x":150,"y":100})
        page.mouse.wheel(0,20)
        page.wait_for_timeout(120)
        assert h.evaluate('e=>e.scrollTop') > 500
        assert brain.evaluate('e=>e.scrollTop') == 100
        page.mouse.wheel(0,-2)
        page.wait_for_timeout(120)
        assert h.evaluate('e=>e.scrollTop') < 520, "Small reversal must respond immediately"
        history_top=h.evaluate('e=>e.scrollTop')
        brain.hover(position={"x":150,"y":120})
        page.mouse.wheel(0,30)
        page.wait_for_timeout(120)
        assert brain.evaluate('e=>e.scrollTop') > 100
        assert h.evaluate('e=>e.scrollTop') == history_top
        brain.evaluate('e=>e.scrollTop=0')
        # The panel is a reading surface: the wheel works there without a click,
        # even while the opening gesture still carries momentum, and swiping up
        # to its top edge stops there instead of collapsing the panel.
        page.mouse.wheel(0,-100)
        page.wait_for_timeout(200)
        assert page.evaluate('__dndImmersive.getState().reveal') == 300
        assert brain.evaluate('e=>e.scrollTop') == 0
        assert h.evaluate('e=>e.scrollTop') == history_top
        page.mouse.wheel(0,200)
        page.wait_for_timeout(250)
        assert brain.evaluate('e=>e.scrollTop') > 0
        assert h.evaluate('e=>e.scrollTop') == history_top
        composer.hover()
        page.mouse.wheel(0,-100)
        page.wait_for_function('__dndImmersive.getState().reveal===0')
        page.wait_for_timeout(250)
        # Pagination prepends six turns while the prior first turn stays put.
        for expected in (24,36,40):
            h.evaluate('e=>e.scrollTop=0')
            anchor=h.locator('[class*="st-key-page2_chat_turn_"]').first
            anchor_class=anchor.get_attribute('class').split('st-key-page2_chat_turn_')[1].split()[0]
            old_offset=anchor.bounding_box()['y']-h.bounding_box()['y']
            h.hover(position={"x":150,"y":100})
            page.mouse.wheel(0,-40)
            expect(page.locator('[data-testid=stChatMessage]')).to_have_count(expected)
            page.wait_for_timeout(350)
            restored=h.locator('.st-key-page2_chat_turn_'+anchor_class)
            new_offset=restored.bounding_box()['y']-h.bounding_box()['y']
            assert abs(new_offset-old_offset)<3,(old_offset,new_offset)
            assert h.evaluate('e=>e.scrollTop')>100
        assert page.locator('.st-key-page2_load_earlier button').is_disabled()
        # Real send/undo use the original backend callbacks.
        n = page.locator("[data-testid=stChatMessage]").count()
        input_box = page.locator(".st-key-page2_chat_input textarea")
        input_box.fill("正式界面发送验证")
        input_box.press("Enter")
        expect(page.locator("[data-testid=stChatMessage]")).to_have_count(n + 2, timeout=15000)
        expect(h).to_contain_text("正式消息接口的本地模拟回复", timeout=15000)
        assert abs(h.bounding_box()["height"] - 520) < 1
        page.locator(".st-key-page2_chat_undo_btn button").click()
        expect(page.locator("[data-testid=stChatMessage]")).to_have_count(n, timeout=10000)
        assert not page.locator("[data-testid=stException]").count()
        assert not errors, errors
        page.screenshot(path="/tmp/dnd-immersive-accepted.png")
        # A full reopen starts at half-screen, not the last dragged height.
        page.reload()
        page.locator("#dnd-chat-resize").wait_for()
        expect(page.locator(".st-key-page2_chat_history")).to_have_css("height", "450px")
        browser.close()
        print("PASS real Page2: live veil while dragging, animated drawers and center layout, half-screen default, resize, pinned composer, native drawers, opacity, background choices, boundary gestures, send/undo, reload, no JS errors")


if __name__ == "__main__":
    run()
