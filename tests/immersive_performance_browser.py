"""Retained background, entry transition, and navigation acceptance.

Run against immersive_app.py; set TEST_URL and DND_UI_FIXTURE_VIDEO.
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1512, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Record first-paint opacity before any application script runs.
        page.add_init_script("""window.entrySamples=[];
          function sampleEntry(){
            const pane=document.querySelector('.st-key-page2_dialogue_pane');
            if(pane)entrySamples.push({ready:document.body.classList.contains('dnd-game-ready'),opacity:Number(getComputedStyle(pane).opacity),height:pane.getBoundingClientRect().height});
            if(!entrySamples.some(s=>s.ready&&s.opacity===1))requestAnimationFrame(sampleEntry);
          }requestAnimationFrame(sampleEntry);""")
        page.goto(os.environ.get("TEST_URL", "http://127.0.0.1:8567"))
        page.wait_for_load_state("networkidle")
        page.wait_for_function("document.body.classList.contains('dnd-game-ready')")
        page.wait_for_function("entrySamples.some(s=>s.ready&&s.opacity===1)")
        samples = page.evaluate("entrySamples")
        assert all(s["opacity"] == 0 or s["height"] <= 1 for s in samples if not s["ready"]), samples
        assert any(0 < s["opacity"] < 1 for s in samples), "Entry must visibly fade"
        page.wait_for_function("document.querySelector('#dnd-immersive-bg img').classList.contains('dnd-media-ready')")
        page.evaluate("""()=>{
          window.canvasCost={draws:0,reads:0};
          for(const [method,key] of [['drawImage','draws'],['getImageData','reads']]){
            const original=CanvasRenderingContext2D.prototype[method];
            CanvasRenderingContext2D.prototype[method]=function(...args){canvasCost[key]++;return original.apply(this,args)};
          }
          window.initialShell=__dndImmersive;
        }""")

        def assert_no_redraw(label):
            cost = page.evaluate("canvasCost")
            assert cost == {"draws": 0, "reads": 0}, (label, cost)

        page.wait_for_timeout(1200)
        assert_no_redraw("idle image")
        h = page.locator(".st-key-page2_chat_history")
        page.locator('.st-key-page2_chat_composer').evaluate("e=>e.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:100}))")
        page.wait_for_function("__dndImmersive.getState().reveal===300")
        page.wait_for_timeout(380)
        page.locator('.st-key-page2_chat_composer').evaluate("e=>e.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-100}))")
        page.wait_for_function("__dndImmersive.getState().reveal===0")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(400)
        assert_no_redraw("brain transitions and window resize")
        # Verify the derived veil/resize geometry without measuring every frame.
        geometry = page.evaluate("""()=>{
          const h=document.querySelector('.st-key-page2_chat_history').getBoundingClientRect();
          const v=document.querySelector('#dnd-immersive-veil').getBoundingClientRect();
          const r=document.querySelector('#dnd-chat-resize').getBoundingClientRect();
          return [Math.abs(h.top-v.top),Math.abs(h.top-9-r.top)];
        }""")
        assert max(geometry) < 2, geometry

        video_path = Path(os.environ["DND_UI_FIXTURE_VIDEO"]).resolve()
        page.route("**/retained-test.mp4", lambda route: route.fulfill(path=str(video_path), content_type="video/mp4"))
        page.evaluate("""()=>{
          window.videoConfig={scope:__dndImmersive.getState().scope,recordId:'',veil:.6,
            background:{id:'test-video',kind:'video',src:'/retained-test.mp4'}};
          __dndImmersive.update(videoConfig);
        }""")
        video = page.locator('#dnd-immersive-bg video')
        page.wait_for_function("document.querySelector('#dnd-immersive-bg video').classList.contains('dnd-media-ready')")
        assert page.evaluate("canvasCost.draws>0"), "First frame should build the surround"
        page.evaluate("canvasCost.draws=canvasCost.reads=0")
        t = video.evaluate("v=>v.currentTime")
        page.wait_for_timeout(1200)
        assert not video.evaluate("v=>v.paused")
        assert video.evaluate("v=>v.currentTime") != t, "Native video must keep playing"
        assert_no_redraw("playing video")
        page.get_by_role("button", name="开启背景声音", exact=True).click()
        page.evaluate("__dndImmersive.update(videoConfig)")
        assert not video.evaluate("v=>v.muted"), "Unchanged config must retain audio state"
        assert page.evaluate("__dndImmersive===initialShell")
        page.get_by_role("button", name="暂停背景视频", exact=True).click()
        page.get_by_role("button", name="播放背景视频", exact=True).click()
        page.wait_for_timeout(400)
        assert_no_redraw("same background, pause and resume")

        # A real native-widget rerun must retain the shell and entry state.
        page.get_by_role("button", name="游戏设置", exact=True).click()
        expect(page.get_by_role("button", name="切换测试页面")).to_be_visible()
        assert page.evaluate("__dndImmersive===initialShell")
        assert page.evaluate("!document.querySelector('#dnd-game-transition').classList.contains('dnd-transition-active')")
        page.get_by_role("button", name="切换测试页面").click()
        expect(page.get_by_role("heading", name="其他页面")).to_be_visible()
        page.wait_for_function("!window.__dndImmersive")
        assert page.locator('#dnd-immersive-bg').count() == 0
        page.get_by_role("button", name="返回游戏").click()
        page.wait_for_function("!!window.__dndImmersive && document.body.classList.contains('dnd-game-ready')")
        expect(h).to_have_css("opacity", "1")
        page.wait_for_timeout(650)
        assert page.locator('#dnd-immersive-bg').count() == 1
        assert not errors, errors
        assert page.locator('[data-testid=stException]').count() == 0
        page.screenshot(path="/tmp/dnd-light-game.png")

        # Reduced motion still reaches the usable final state.
        page.emulate_media(reduced_motion="reduce")
        page.reload()
        page.wait_for_function("document.body.classList.contains('dnd-game-ready')")
        expect(h).to_have_css("transition-duration", "0s")
        browser.close()
        print("PASS: smooth entry, retained backgrounds, zero steady-state canvas work during playback, native playback controls, navigation cleanup, reduced motion")


if __name__ == "__main__":
    run()
