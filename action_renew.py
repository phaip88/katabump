import os
import json
import asyncio
import re
import requests
import time
import random
from urllib.parse import urlparse
from camoufox.async_api import AsyncCamoufox

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
PROXY_URL = os.environ.get('PROXY_URL')
PROXY_CONFIG = {"server": "http://127.0.0.1:8080"} if PROXY_URL else None

def send_tg_message(text, photo_path=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/"
    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, 'rb') as f:
                requests.post(url + "sendPhoto", data={"chat_id": TG_CHAT_ID, "caption": text[:1024]}, files={"photo": f}, timeout=30)
        else:
            requests.post(url + "sendMessage", data={"chat_id": TG_CHAT_ID, "text": text[:4096]}, timeout=30)
    except Exception as e:
        print(f"TG notification failed: {e}")

async def wait_for_turnstile(page):
    print("等待 Turnstile 验证 (注入真实补丁并点击 iframe)...")
    patch_code = """
    (() => {
        if (window.__patched_mouse) return;
        window.__patched_mouse = true;
        Object.defineProperty(MouseEvent.prototype, 'screenX', {
            get: function() { return (this.clientX || 0) + (window.screenX || 0) + Math.floor(Math.random()*10); }
        });
        Object.defineProperty(MouseEvent.prototype, 'screenY', {
            get: function() { return (this.clientY || 0) + (window.screenY || 0) + Math.floor(Math.random()*10); }
        });
    })();
    """
    await page.add_init_script(patch_code)
    try:
        await page.evaluate(patch_code)
    except:
        pass

    for _ in range(25):
        try:
            x = random.randint(300, 800)
            y = random.randint(200, 600)
            await page.mouse.move(x, y, steps=5)
        except:
            pass

        try:
            frames = page.frames
            for f in frames:
                if 'cloudflare' in f.url:
                    await f.evaluate(patch_code)
            
            cf_iframe = page.frame_locator('iframe[src*="cloudflare"]').locator('body')
            if await cf_iframe.count() > 0:
                print(f"[Turnstile] 找到 {await cf_iframe.count()} 个 iframe body，尝试点击...")
                await cf_iframe.first.click(force=True, delay=random.randint(50, 150))
                # 尝试点击里面的复选框
                checkbox = page.frame_locator('iframe[src*="cloudflare"]').locator('input[type="checkbox"]')
                if await checkbox.count() > 0:
                    print("[Turnstile] 找到内部 checkbox，尝试强制点击...")
                    await checkbox.first.click(force=True)
                else:
                    print("[Turnstile] 未找到内部 checkbox，可能处于无感知验证模式。")
            else:
                print("[Turnstile] 未找到 cloudflare iframe。")
        except Exception as e:
            print(f"[Turnstile] iframe 交互出错: {e}")

        val = await page.evaluate("() => { const el = document.querySelector('input[name=\"cf-turnstile-response\"]'); return el ? el.value : null; }")
        if val and len(val) > 20:
            print("Turnstile 已自动完成！")
            return True
        await asyncio.sleep(1)
    return False

async def process_user(user, browser):
    username = user.get('username')
    password = user.get('password')
    print(f"\n========== 开始处理: {username} ==========")
    context = await browser.new_context()
    page = await context.new_page()
    safe_user = re.sub(r'[^a-z0-9]', '_', username.lower())
    os.makedirs('screenshots', exist_ok=True)
    
    try:
        await page.goto("https://dashboard.katabump.com/auth/login", timeout=60000, wait_until="domcontentloaded")
        
        # 5秒盾 (UAM) 前置检测与等待
        try:
            if "Just a moment" in await page.title() or await page.locator('#challenge-running').is_visible():
                print("检测到 Cloudflare 5秒前置盾 (UAM)，正在等待自适应通过...")
                await page.wait_for_selector('input[type="email"]', timeout=25000, state="visible")
                print("5秒前置盾已通过，进入真实页面。")
        except Exception:
            pass

        await page.fill('input[type="email"]', username)
        await page.fill('input[type="password"]', password)
        
        await asyncio.sleep(2)
        if await page.get_by_text("Troubleshoot", exact=True).is_visible():
            print("检测到 Cloudflare Troubleshoot 封禁页面！")
            screenshot_path = f"screenshots/{safe_user}_troubleshoot.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            send_tg_message(f"⚠️ 登录失败 (被 CF 彻底拦截)\n用户: {username}", screenshot_path)
            return

        ts_passed = await wait_for_turnstile(page)
        if not ts_passed:
            print("⚠️ Turnstile 响应超时，强制尝试点击 Login...")

        login_btn = page.get_by_role("button", name="Login", exact=True).first
        await login_btn.click()
        await asyncio.sleep(2)

        if await page.get_by_text("Please complete captcha").is_visible():
            print("登录失败: 要求人机验证 (Please complete captcha)")
            screenshot_path = f"screenshots/{safe_user}_captcha_fail.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            send_tg_message(f"⚠️ 登录失败 (需过盾)\n用户: {username}", screenshot_path)
            return

        if await page.get_by_text("These credentials do not match").is_visible():
            print("登录失败: 密码或账号错误")
            send_tg_message(f"⚠️ 登录失败 (账号密码错误)\n用户: {username}")
            return
            
        print("成功发起登录，等待 'See' 按钮...")
        try:
            see_link = page.get_by_role("link", name="See").first
            await see_link.wait_for(timeout=20000, state="visible")
            await see_link.click()
        except:
            print("未找到 See 按钮，可能登录未成功。")
            screenshot_path = f"screenshots/{safe_user}_see_not_found.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            send_tg_message(f"⚠️ 处理未完成\n用户: {username}\n原因: 未找到 See 链接", screenshot_path)
            return

        print("进入控制面板，寻找 Renew 按钮...")
        renew_btn = page.get_by_role("button", name="Renew", exact=True).first
        try:
            await renew_btn.wait_for(timeout=10000, state="visible")
            await renew_btn.click()
            print("Renew 按钮已点击。等待模态框...")
        except:
            print("未找到 Renew 按钮，可能已无服务器。")
            send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 未找到 Renew 按钮")
            return

        await asyncio.sleep(2)
        modal = page.locator('#renew-modal')
        try:
            await modal.wait_for(timeout=5000, state="visible")
        except:
            pass

        not_time = page.get_by_text("You can't renew your server yet")
        if await not_time.is_visible():
            txt = await not_time.inner_text()
            match = re.search(r'as of\s+(.*?)\s+\(', txt)
            date_str = match.group(1) if match else 'Unknown'
            print(f"暂无法续期。下次可用: {date_str}")
            screenshot_path = f"screenshots/{safe_user}_skip.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            send_tg_message(f"⏳ 暂无法续期（跳过）\n用户: {username}\n原因: 还没到时间\n下次可用: {date_str}", screenshot_path)
            return

        print("检查弹窗内是否有 Altcha...")
        try:
            await page.evaluate('''() => {
                const w = document.querySelector('altcha-widget');
                if(w && w.shadowRoot) {
                    const cb = w.shadowRoot.querySelector('input[type="checkbox"]');
                    if(cb && !cb.checked) cb.click();
                }
            }''')
            for _ in range(15):
                state = await page.evaluate("() => { const w = document.querySelector('altcha-widget'); return w ? (w.state || w.getAttribute('state')) : ''; }")
                if state == 'verified':
                    print("✅ ALTCHA 验证通过！")
                    break
                await asyncio.sleep(1)
        except Exception as e:
            pass

        ts_passed_in_modal = await wait_for_turnstile(page)

        confirm_btn = modal.get_by_role("button", name="Renew").first
        if await confirm_btn.is_visible():
            screenshot_path = f"screenshots/{safe_user}_before_confirm.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            
            print("点击 Renew 确认按钮...")
            await confirm_btn.click()
            
            await asyncio.sleep(3)
            if await page.get_by_text("Please complete the captcha to continue").is_visible():
                print("确认时遭遇 Captcha 错误。")
                send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 确认阶段提示 Captcha 错误")
                return
                
            print("✅ 续期请求处理完成。")
            final_path = f"screenshots/{safe_user}_success.png"
            await page.screenshot(path=final_path, full_page=True)
            send_tg_message(f"✅ 续期尝试完成\n用户: {username}", final_path)
            
    except Exception as e:
        print(f"发生异常: {e}")
        screenshot_path = f"screenshots/{safe_user}_error.png"
        try:
            await page.screenshot(path=screenshot_path, full_page=True)
        except:
            pass
        send_tg_message(f"❌ 运行异常\n用户: {username}\n原因: {str(e)}", screenshot_path)
    finally:
        await context.close()

async def main():
    users_json = os.environ.get('USERS_JSON', '[]')
    try:
        users = json.loads(users_json)
    except:
        users = []
        
    if not users:
        print("未在 USERS_JSON 中找到用户配置。")
        return

    print("启动 Camoufox 浏览器...")
    async with AsyncCamoufox(headless=False, proxy=PROXY_CONFIG) as browser:
        for user in users:
            await process_user(user, browser)

if __name__ == "__main__":
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        import random
        delay = random.randint(0, 3 * 3600)
        print(f"[Anti-Detection] Scheduled run: 延迟 {delay} 秒...")
        time.sleep(delay)
    
    asyncio.run(main())
