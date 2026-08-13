import os
import json
import time
import re
import requests
from urllib.parse import urlparse
from DrissionPage import Chromium, ChromiumOptions

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
PROXY_URL = os.environ.get('PROXY_URL')
# For DrissionPage, if PROXY_URL is given, we can use the local sing-box HTTP proxy
PROXY_SERVER = "127.0.0.1:8080" if PROXY_URL else None

def send_tg_message(text, photo_path=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/"
    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, 'rb') as f:
                requests.post(url + "sendPhoto", data={"chat_id": TG_CHAT_ID, "caption": text}, files={"photo": f}, timeout=10)
        else:
            requests.post(url + "sendMessage", data={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"Telegram 推送失败: {e}")

def solve_turnstile(page):
    print("开始检测并尝试处理 Turnstile...")
    try:
        page.run_js("try { turnstile.reset() } catch(e) { }")
    except:
        pass

    for i in range(25):
        try:
            turnstileResponse = page.run_js("try { return turnstile.getResponse() } catch(e) { return null }")
            if turnstileResponse and len(turnstileResponse) > 20:
                print("Turnstile 验证已通过！")
                return True
            
            challengeSolution = page.ele("@name=cf-turnstile-response", timeout=2)
            if not challengeSolution:
                time.sleep(1)
                continue

            challengeWrapper = challengeSolution.parent()
            challengeIframe = challengeWrapper.shadow_root.ele("tag:iframe", timeout=2)
            if not challengeIframe:
                time.sleep(1)
                continue
            
            # Inject patch directly into the Turnstile iframe
            challengeIframe.run_js("""
                window.dtp = 1
                function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
                let screenX = getRandomInt(800, 1200);
                let screenY = getRandomInt(400, 600);
                Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
                Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });
            """)
            
            challengeIframeBody = challengeIframe.ele("tag:body", timeout=2).shadow_root
            challengeButton = challengeIframeBody.ele("tag:input", timeout=2)
            if challengeButton:
                print("点击 Turnstile Checkbox...")
                challengeButton.click()
                time.sleep(2)
        except Exception as e:
            pass
        time.sleep(1)
    
    print("Turnstile 验证超时！")
    return False

def process_user(user, browser):
    username = user.get('username')
    password = user.get('password')
    print(f"\n========== 开始处理: {username} ==========")
    
    page = browser.new_tab()
    safe_user = re.sub(r'[^a-z0-9]', '_', username.lower())
    os.makedirs('screenshots', exist_ok=True)
    
    try:
        page.get("https://dashboard.katabump.com/auth/login", timeout=60)
        
        # 5秒盾前置检测
        if "Just a moment" in page.title or page.ele('#challenge-running', timeout=2):
            print("检测到 Cloudflare 5秒前置盾，等待通过...")
            page.wait.ele_loaded('css:input[type="email"]', timeout=30)
            print("5秒前置盾已通过")

        # 使用 JS 强制修改 React 输入框的值
        react_setter_js = """
            function setNativeValue(element, value) {
                const valueSetter = Object.getOwnPropertyDescriptor(element, 'value').set;
                const prototype = Object.getPrototypeOf(element);
                const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
                
                if (valueSetter && valueSetter !== prototypeValueSetter) {
                    prototypeValueSetter.call(element, value);
                } else {
                    valueSetter.call(element, value);
                }
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }
            let emailEl = document.querySelector('input[type="email"]');
            let pwdEl = document.querySelector('input[type="password"]');
            if (emailEl) setNativeValue(emailEl, arguments[0]);
            if (pwdEl) setNativeValue(pwdEl, arguments[1]);
        """
        page.run_js(react_setter_js, username, password)
        print("已使用 JS 填入账号密码。")
        time.sleep(2)
        
        if page.ele("text:Troubleshoot", timeout=1):
            print("检测到 Troubleshoot 封禁页面！")
            screenshot_path = f"screenshots/{safe_user}_troubleshoot.png"
            page.get_screenshot(path=screenshot_path, full_page=True)
            send_tg_message(f"⚠️ 登录失败 (被CF彻底拦截)\n用户: {username}", screenshot_path)
            page.close()
            return

        ts_passed = solve_turnstile(page)
        if not ts_passed:
            print("⚠️ Turnstile 响应超时，强制尝试点击 Login...")

        login_btn = page.ele('text:Login')
        if login_btn:
            # 再次强制赋值以防 Turnstile 导致表单重置
            page.run_js(react_setter_js, username, password)
            print("点击 Login 按钮...")
            login_btn.click()
            time.sleep(3)

        if page.ele("text:Please complete captcha", timeout=3):
            print("登录失败: 要求人机验证 (Please complete captcha)")
            screenshot_path = f"screenshots/{safe_user}_captcha_fail.png"
            page.get_screenshot(path=screenshot_path)
            send_tg_message(f"⚠️ 登录失败 (需过盾)\n用户: {username}", screenshot_path)
            page.close()
            return

        if page.ele("text:These credentials do not match", timeout=1):
            print("登录失败: 密码或账号错误")
            send_tg_message(f"⚠️ 登录失败 (账号密码错误)\n用户: {username}")
            page.close()
            return
            
        print("成功发起登录，等待 'See' 按钮...")
        see_link = page.ele("text:See", timeout=20)
        if see_link:
            see_link.click()
        else:
            print("未找到 See 按钮，可能登录未成功。")
            screenshot_path = f"screenshots/{safe_user}_see_not_found.png"
            page.get_screenshot(path=screenshot_path)
            with open(f"screenshots/{safe_user}_page.html", "w", encoding="utf-8") as f:
                f.write(page.html)
            send_tg_message(f"⚠️ 处理未完成\n用户: {username}\n原因: 未找到 See 链接", screenshot_path)
            page.close()
            return

        print("进入控制面板，寻找 Renew 按钮...")
        renew_btn = page.ele("text:Renew", timeout=10)
        if renew_btn:
            renew_btn.click()
            print("Renew 按钮已点击。等待模态框...")
        else:
            print("未找到 Renew 按钮，可能已无服务器。")
            send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 未找到 Renew 按钮")
            page.close()
            return

        time.sleep(2)
        
        not_time = page.ele("text:You can't renew your server yet", timeout=2)
        if not_time:
            txt = not_time.text
            match = re.search(r'as of\s+(.*?)\s+\(', txt)
            date_str = match.group(1) if match else 'Unknown'
            print(f"暂无法续期。下次可用: {date_str}")
            screenshot_path = f"screenshots/{safe_user}_skip.png"
            page.get_screenshot(path=screenshot_path)
            send_tg_message(f"ℹ️ 暂无法续期（跳过）\n用户: {username}\n原因: 还没到时间\n下次可用: {date_str}", screenshot_path)
            page.close()
            return

        print("检查弹窗内是否有 Altcha...")
        try:
            altcha_widget = page.ele('tag:altcha-widget', timeout=2)
            if altcha_widget:
                cb = altcha_widget.shadow_root.ele('tag:input')
                if cb:
                    cb.click()
                    for _ in range(15):
                        state = altcha_widget.attr('state')
                        if state == 'verified':
                            print("✅ ALTCHA 验证通过！")
                            break
                        time.sleep(1)
        except Exception as e:
            pass

        ts_passed_in_modal = solve_turnstile(page)

        confirm_btn = page.ele('css:#renew-modal button:contains("Renew")', timeout=2)
        if not confirm_btn:
            confirm_btn = page.ele('text:Renew', index=2, timeout=2) # fallback to the second Renew button

        if confirm_btn:
            screenshot_path = f"screenshots/{safe_user}_before_confirm.png"
            page.get_screenshot(path=screenshot_path)
            
            print("点击 Renew 确认按钮...")
            confirm_btn.click()
            
            time.sleep(3)
            if page.ele("text:Please complete the captcha to continue", timeout=2):
                print("确认时遭遇 Captcha 错误。")
                send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 确认阶段提示 Captcha 错误")
                page.close()
                return
                
            print("✅ 续期请求处理完成。")
            final_path = f"screenshots/{safe_user}_success.png"
            page.get_screenshot(path=final_path)
            send_tg_message(f"✅ 续期尝试完成\n用户: {username}", final_path)
            
    except Exception as e:
        print(f"发生异常: {e}")
        screenshot_path = f"screenshots/{safe_user}_error.png"
        try:
            page.get_screenshot(path=screenshot_path)
        except:
            pass
        send_tg_message(f"❌ 运行异常\n用户: {username}\n原因: {str(e)}", screenshot_path)
    finally:
        page.close()

def main():
    users_json = os.environ.get('USERS_JSON', '[]')
    try:
        users = json.loads(users_json)
    except:
        users = []
        
    if not users:
        print("未在 USERS_JSON 中找到用户配置。")
        return

    print("启动 Chromium (DrissionPage)...")
    co = ChromiumOptions()
    co.auto_port()
    # Apply extension patch at document start level
    EXTENSION_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "CDP_patcher", "turnstilePatch"))
    if os.path.exists(EXTENSION_PATH):
        co.add_extension(EXTENSION_PATH)
        print(f"Loaded Turnstile CDP extension from {EXTENSION_PATH}")
    
    if PROXY_SERVER:
        co.set_proxy(PROXY_SERVER)
        print(f"Using proxy: {PROXY_SERVER}")
        
    # Anti-detection settings for Chromium
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--start-maximized')
    
    browser = Chromium(co)

    for user in users:
        process_user(user, browser)
        
    browser.quit()

if __name__ == "__main__":
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        import random
        delay = random.randint(0, 3 * 3600)
        print(f"[Anti-Detection] Scheduled run: 延迟 {delay} 秒...")
        time.sleep(delay)
    
    main()
