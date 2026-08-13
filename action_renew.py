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

    wait_time = 0
    has_clicked = False
    
    while wait_time < 25:
        if not has_clicked:
            try:
                # 使用 asyncio.wait_for 防止 CF 死锁浏览器主线程导致一直挂起
                box = await asyncio.wait_for(page.evaluate('''() => {
                    const getIframe = () => {
                        let iframe = document.querySelector('iframe[src*="cloudflare.com"]') || document.querySelector('iframe[src*="turnstile"]');
                        if (iframe) return iframe;
                        
                        const tsInputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                        for (const input of tsInputs) {
                            let current = input.parentElement;
                            for (let i = 0; i < 3; i++) {
                                if (!current) break;
                                const frame = current.querySelector('iframe');
                                if (frame) return frame;
                                current = current.parentElement;
                            }
                        }
                        return null;
                    };
                    
                    const iframe = getIframe();
                    if (iframe) {
                        const rect = iframe.getBoundingClientRect();
                        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                    }
                    
                    // Fallback 坐标：根据最新截图特征，验证框在邮箱密码输入框正下方
                    // 固定点击区域中心约莫 (640, 480) 附近，这只是兜底
                    const passwordInput = document.querySelector('input[type="password"]');
                    if (passwordInput) {
                         const pRect = passwordInput.getBoundingClientRect();
                         return { x: pRect.x, y: pRect.y + 60, width: 300, height: 65 };
                    }
                    return null;
                }'''), timeout=5.0)

                if box and box.get('width', 0) > 0:
                    print(f"[Turnstile] JS 获取到盾块或兜底区域坐标: {box}，执行多段仿生游走...")
                    cx = box['x'] + box['width'] / 2 + random.uniform(-5, 5)
                    cy = box['y'] + box['height'] / 2 + random.uniform(-2, 2)
                    
                    # 打破直线：生成 2 个中间途经点
                    start_x, start_y = random.randint(100, 300), random.randint(100, 300)
                    await asyncio.wait_for(page.mouse.move(start_x, start_y, steps=5), timeout=2.0)
                    
                    mid_x = (start_x + cx) / 2 + random.randint(-50, 50)
                    mid_y = (start_y + cy) / 2 + random.randint(-50, 50)
                    await asyncio.wait_for(page.mouse.move(mid_x, mid_y, steps=10), timeout=2.0)
                    
                    await asyncio.wait_for(page.mouse.move(cx, cy, steps=10), timeout=2.0)
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                    
                    await asyncio.wait_for(page.mouse.down(), timeout=2.0)
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                    await asyncio.wait_for(page.mouse.up(), timeout=2.0)
                    
                    print("[Turnstile] 单次物理点击完成，移出焦点并进入纯净轮询等待...")
                    away_x = cx + random.randint(100, 200) * random.choice([1, -1])
                    away_y = cy + random.randint(100, 200) * random.choice([1, -1])
                    await asyncio.wait_for(page.mouse.move(away_x, away_y, steps=10), timeout=2.0)
                    
                    has_clicked = True
                else:
                    # 没获取到坐标，可能是由于延迟加载，本轮跳过，下一次继续尝试找坐标
                    print("[Turnstile] 当前DOM未就绪，尚未获取可用坐标，延后至下一秒重试...")
            except asyncio.TimeoutError:
                print("[Turnstile] 获取坐标或执行鼠标动作时发生严重挂起 (TimeoutError)！")
            except Exception as e:
                print(f"[Turnstile] 交互逻辑出错: {e}")
        
        try:
            val = await asyncio.wait_for(
                page.evaluate("() => { const el = document.querySelector('input[name=\"cf-turnstile-response\"]'); return el ? el.value : null; }"),
                timeout=3.0
            )
            if val and len(val) > 20:
                print("Turnstile 已自动完成！")
                return True
        except asyncio.TimeoutError:
            print("[Turnstile] 检查 Token 时浏览器主线程无响应 (TimeoutError)...")
        except Exception:
            pass
        
        await asyncio.sleep(1)
        wait_time += 1
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
        await login_btn.click(timeout=10000)
        await asyncio.sleep(2)

        if await page.get_by_text("Please complete captcha").is_visible():
            print("登录失败: 要求人机验证 (Please complete captcha)")
            screenshot_path = f"screenshots/{safe_user}_captcha_fail.png"
            await page.screenshot(path=screenshot_path, full_page=False, timeout=10000)
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
            await see_link.click(timeout=10000)
        except:
            print("未找到 See 按钮，可能登录未成功。")
            screenshot_path = f"screenshots/{safe_user}_see_not_found.png"
            await page.screenshot(path=screenshot_path, full_page=False, timeout=10000)
            send_tg_message(f"⚠️ 处理未完成\n用户: {username}\n原因: 未找到 See 链接", screenshot_path)
            return

        print("进入控制面板，寻找 Renew 按钮...")
        renew_btn = page.get_by_role("button", name="Renew", exact=True).first
        try:
            await renew_btn.wait_for(timeout=10000, state="visible")
            await renew_btn.click(timeout=10000)
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
            await page.screenshot(path=screenshot_path, full_page=False, timeout=10000)
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
            await page.screenshot(path=screenshot_path, full_page=False, timeout=10000)
            
            print("点击 Renew 确认按钮...")
            await confirm_btn.click(timeout=10000)
            
            await asyncio.sleep(3)
            if await page.get_by_text("Please complete the captcha to continue").is_visible():
                print("确认时遭遇 Captcha 错误。")
                send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 确认阶段提示 Captcha 错误")
                return
                
            print("✅ 续期请求处理完成。")
            final_path = f"screenshots/{safe_user}_success.png"
            await page.screenshot(path=final_path, full_page=False, timeout=10000)
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
