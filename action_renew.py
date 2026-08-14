import os
import json
import time
import re
import requests
import hashlib
import base64
import sys
import random
from urllib.parse import urlparse
from DrissionPage import Chromium, ChromiumOptions

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
PROXY_URL = os.environ.get('PROXY_URL')
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

def solve_altcha_pow(challenge_data):
    try:
        algorithm = challenge_data.get('algorithm', 'SHA-256')
        challenge = challenge_data.get('challenge')
        salt = challenge_data.get('salt')
        signature = challenge_data.get('signature')
        maxnumber = challenge_data.get('maxnumber', 1000000)

        for num in range(maxnumber + 1):
            test_str = f"{salt}{num}"
            h = hashlib.sha256(test_str.encode('utf-8')).hexdigest()
            if h == challenge:
                payload = {
                    "algorithm": algorithm,
                    "challenge": challenge,
                    "number": num,
                    "salt": salt,
                    "signature": signature
                }
                return base64.b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8')
    except Exception as e:
        print(f"Altcha PoW 求解出错: {e}")
    return None

def solve_turnstile(page, timeout_sec=25):
    # Quick check if turnstile response field or container exists
    ts_exists = page.ele('@name=cf-turnstile-response', timeout=2) or page.ele('css:.cf-turnstile', timeout=1)
    if not ts_exists:
        return False

    print("开始检测并尝试处理 Turnstile...")
    try:
        page.run_js("try { turnstile.reset() } catch(e) { }")
    except:
        pass

    for i in range(timeout_sec):
        try:
            turnstileResponse = page.run_js("try { return turnstile.getResponse() } catch(e) { return null }")
            if turnstileResponse and len(turnstileResponse) > 20:
                print("Turnstile 验证已通过！")
                return True
            
            challengeSolution = page.ele("@name=cf-turnstile-response", timeout=1)
            if not challengeSolution:
                time.sleep(1)
                continue

            challengeWrapper = challengeSolution.parent()
            challengeIframe = challengeWrapper.shadow_root.ele("tag:iframe", timeout=1)
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
            
            challengeIframeBody = challengeIframe.ele("tag:body", timeout=1).shadow_root
            challengeButton = challengeIframeBody.ele("tag:input", timeout=1)
            if challengeButton:
                print("点击 Turnstile Checkbox...")
                challengeButton.click()
                time.sleep(2)
        except Exception as e:
            pass
        time.sleep(1)
    
    print("Turnstile 验证超时！")
    return False

def handle_altcha(page):
    print("检查弹窗内是否有 Altcha...")
    try:
        altcha_widget = page.ele('css:#renew-modal altcha-widget', timeout=5)
        if not altcha_widget:
            altcha_widget = page.ele('tag:altcha-widget', timeout=2)

        if not altcha_widget:
            print("未发现 Altcha 组件。")
            return True

        print("找到了 Altcha 组件，等待其加载与可见...")
        altcha_widget.wait.displayed(timeout=3)

        # 1. 模拟真实用户交互点击 Altcha 组件内部 label / checkbox
        page.run_js("""
            (() => {
                const widget = document.querySelector('#renew-modal altcha-widget') || document.querySelector('altcha-widget');
                if (!widget) return;
                const root = widget.shadowRoot || widget;
                const target = root.querySelector('label') || root.querySelector('.altcha-checkbox') || root.querySelector('input[type="checkbox"]') || widget;
                if (target) {
                    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evt => {
                        target.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, composed: true, view: window }));
                    });
                    if (typeof target.click === 'function') target.click();
                }
            })();
        """)
        
        # 2. 轮询检测是否已原生计算完成验证
        for _ in range(6):
            time.sleep(1)
            status = page.run_js("""
                (() => {
                    const widget = document.querySelector('#renew-modal altcha-widget') || document.querySelector('altcha-widget');
                    if (!widget) return { state: 'not_found' };
                    const root = widget.shadowRoot || widget;
                    const container = root.querySelector('.altcha');
                    const state = (container && container.getAttribute('data-state')) || widget.getAttribute('data-state') || widget.state;
                    const val = widget.value || (root.querySelector('input[name="altcha"]') ? root.querySelector('input[name="altcha"]').value : null);
                    return { state: state, hasValue: !!(val && val.length > 10) };
                })();
            """)
            if status and (status.get('state') == 'verified' or status.get('hasValue')):
                print("✅ ALTCHA 原生算力验证通过！")
                return True

        # 3. 兜底策略：调用 Python 进行极速 PoW 算法求解并注入 Payload
        print("ALTCHA 原生未完成，启动 Python 极速 PoW 算法求解与注入...")
        challenge_url = page.run_js("""
            (() => {
                const widget = document.querySelector('#renew-modal altcha-widget') || document.querySelector('altcha-widget');
                return widget ? (widget.getAttribute('challengeurl') || widget.challengeurl) : null;
            })();
        """) or "https://altcha.katabump.fr/challenge"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
        resp = requests.get(challenge_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            challenge_data = resp.json()
            payload_b64 = solve_altcha_pow(challenge_data)
            if payload_b64:
                print("PoW 求解成功，注入 Payload 到组件...")
                injected = page.run_js(f"""
                    ((payload) => {{
                        const widget = document.querySelector('#renew-modal altcha-widget') || document.querySelector('altcha-widget');
                        if (!widget) return false;
                        const root = widget.shadowRoot || widget;
                        
                        widget.value = payload;
                        
                        let hiddenInput = root.querySelector('input[name="altcha"]');
                        if (!hiddenInput) {{
                            hiddenInput = document.createElement('input');
                            hiddenInput.type = 'hidden';
                            hiddenInput.name = 'altcha';
                            widget.appendChild(hiddenInput);
                        }}
                        hiddenInput.value = payload;
                        hiddenInput.dispatchEvent(new Event('input', {{ bubbles: true, composed: true }}));
                        hiddenInput.dispatchEvent(new Event('change', {{ bubbles: true, composed: true }}));

                        const container = root.querySelector('.altcha');
                        if (container) container.setAttribute('data-state', 'verified');
                        widget.setAttribute('data-state', 'verified');
                        
                        const cb = root.querySelector('input[type="checkbox"]');
                        if (cb) {{
                            cb.checked = true;
                        }}
                        
                        widget.dispatchEvent(new CustomEvent('verified', {{ detail: {{ payload: payload }}, bubbles: true, composed: true }}));
                        widget.dispatchEvent(new CustomEvent('statechange', {{ detail: {{ state: 'verified' }}, bubbles: true, composed: true }}));
                        return true;
                    }})('{payload_b64}');
                """)
                if injected:
                    print("✅ ALTCHA 算力 Payload 注入完成！")
                    time.sleep(1)
                    return True
    except Exception as e:
        print(f"处理 Altcha 发生异常: {e}")
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

        email_ele = page.ele('css:input[type="email"]')
        if email_ele:
            email_ele.input(username, clear=True)
            
        pwd_ele = page.ele('css:input[type="password"]')
        if pwd_ele:
            pwd_ele.input(password, clear=True)
            
        time.sleep(2)
        
        if page.ele("text:Troubleshoot", timeout=1):
            print("检测到 Troubleshoot 封禁页面！")
            screenshot_path = f"screenshots/{safe_user}_troubleshoot.png"
            page.get_screenshot(path=screenshot_path, full_page=True)
            send_tg_message(f"⚠️ 登录失败 (被CF彻底拦截)\n用户: {username}", screenshot_path)
            page.close()
            return False

        ts_passed = solve_turnstile(page, timeout_sec=25)
        if not ts_passed:
            print("⚠️ Turnstile 响应超时，强制尝试点击 Login...")

        login_btn = page.ele('css:button[id="submit"]')
        if login_btn:
            print("尝试通过 input 输入账号密码...")
            user_input = page.ele('css:input[name="email"]')
            if user_input:
                page.run_js(f"document.querySelector('input[name=\"email\"]').value = '{username}'; document.querySelector('input[name=\"email\"]').dispatchEvent(new Event('input', {{ bubbles: true }}));")
                user_input.input(username, clear=True)
            
            pwd_input = page.ele('css:input[name="password"]')
            if pwd_input:
                page.run_js(f"document.querySelector('input[name=\"password\"]').value = '{password}'; document.querySelector('input[name=\"password\"]').dispatchEvent(new Event('input', {{ bubbles: true }}));")
                pwd_input.input(password, clear=True)
            
            print("点击 Login 按钮...")
            login_btn.click()
            time.sleep(3)

        if page.ele("text:Please complete captcha", timeout=3):
            print("登录失败: 要求人机验证 (Please complete captcha)")
            screenshot_path = f"screenshots/{safe_user}_captcha_fail.png"
            page.get_screenshot(path=screenshot_path)
            send_tg_message(f"⚠️ 登录失败 (需过盾)\n用户: {username}", screenshot_path)
            page.close()
            return False

        if page.ele("text:These credentials do not match", timeout=1):
            print("登录失败: 密码或账号错误")
            send_tg_message(f"⚠️ 登录失败 (账号密码错误)\n用户: {username}")
            page.close()
            return False
            
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
            return False

        print("进入控制面板，寻找 Renew 按钮...")
        renew_btn = page.ele("text:Renew", timeout=10)
        if renew_btn:
            renew_btn.click()
            print("Renew 按钮已点击。等待模态框...")
        else:
            print("未找到 Renew 按钮，可能已无服务器。")
            send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 未找到 Renew 按钮")
            page.close()
            return False

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
            return True

        # 处理弹窗内的 Altcha 验证
        handle_altcha(page)

        # 检查模态框内是否有 Turnstile 备选验证
        if page.ele('@name=cf-turnstile-response', timeout=2):
            solve_turnstile(page, timeout_sec=20)

        # 准确查找模态框内部的 Renew 确认提交按钮
        confirm_btn = None
        modal = page.ele('#renew-modal', timeout=3) or page.ele('css:.modal', timeout=1) or page.ele('css:[role="dialog"]', timeout=1)
        if modal:
            confirm_btn = (modal.ele('xpath:.//button[contains(., "Renew")]', timeout=2) 
                           or modal.ele('tag:button@type=submit', timeout=1)
                           or modal.ele('css:button.btn-primary', timeout=1))
        if not confirm_btn:
            confirm_btn = page.ele('xpath://button[contains(., "Renew")]', timeout=2)

        screenshot_path = f"screenshots/{safe_user}_before_confirm.png"
        page.get_screenshot(path=screenshot_path)
        
        print("点击 Renew 确认按钮...")
        # 1. 使用 JS 精准派发点击事件到弹窗内的 Renew 按钮
        click_res = page.run_js("""
            (() => {
                const modal = document.querySelector('#renew-modal') || document.querySelector('.modal') || document.querySelector('[role="dialog"]') || document.body;
                const buttons = Array.from(modal.querySelectorAll('button'));
                const renewBtn = buttons.find(b => b.textContent.trim().toLowerCase() === 'renew' || b.textContent.includes('Renew'));
                if (renewBtn) {
                    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evt => {
                        renewBtn.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
                    });
                    renewBtn.click();
                    return { success: true, text: renewBtn.textContent.trim(), tag: renewBtn.tagName };
                }
                const form = modal.querySelector('form');
                if (form) {
                    form.submit();
                    return { success: true, form_submitted: true };
                }
                return { success: false, reason: 'button_not_found' };
            })();
        """)
        print(f"Renew 按钮触发结果: {click_res}")

        # 2. 原生点击补充
        if confirm_btn:
            try:
                confirm_btn.click()
            except Exception as ex:
                pass
        
        print("等待续期结果与页面状态更新...")
        time.sleep(6)
        
        final_path = f"screenshots/{safe_user}_success.png"
        page.get_screenshot(path=final_path)

        if page.ele("text:Please complete the captcha to continue", timeout=1):
            print("⚠️ 确认时遭遇 Captcha 错误。")
            send_tg_message(f"⚠️ 续期失败\n用户: {username}\n原因: 确认阶段提示 Captcha 错误", final_path)
            page.close()
            return False
            
        print("✅ 续期请求处理完成。")
        send_tg_message(f"✅ 续期完成\n用户: {username}", final_path)
        return True
            
    except Exception as e:
        print(f"发生异常: {e}")
        import traceback
        traceback.print_exc()
        screenshot_path = f"screenshots/{safe_user}_error.png"
        try:
            page.get_screenshot(path=screenshot_path)
        except:
            pass
        send_tg_message(f"❌ 运行异常\n用户: {username}\n原因: {str(e)}", screenshot_path)
        return False
    finally:
        try:
            page.close()
        except:
            pass

def main():
    users_json = os.environ.get('USERS_JSON', '[]')
    try:
        users = json.loads(users_json)
    except:
        users = []
        
    if not users:
        print("⚠️ USERS_JSON 未提供或解析失败！")
        return

    print("启动 Chromium (DrissionPage)...")
    co = ChromiumOptions()
    co.auto_port()
    EXTENSION_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "CDP_patcher", "turnstilePatch"))
    if os.path.exists(EXTENSION_PATH):
        co.add_extension(EXTENSION_PATH)
        print(f"Loaded Turnstile CDP extension from {EXTENSION_PATH}")
    
    if PROXY_SERVER:
        co.set_proxy(PROXY_SERVER)
        print(f"Using proxy: {PROXY_SERVER}")
        
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--start-maximized')
    
    browser = Chromium(co)

    failed_users = []
    for user in users:
        success = process_user(user, browser)
        if not success:
            failed_users.append(user.get('username'))
        
    browser.quit()
    
    if len(failed_users) == len(users) and len(users) > 0:
        print("\n❌ 所有账号续期均失败，退出代码 1")
        sys.exit(1)
    elif failed_users:
        print(f"\n⚠️ 部分账号续期失败: {', '.join(failed_users)}")
        sys.exit(1)
    else:
        print("\n✅ 所有账号续期成功！")

if __name__ == "__main__":
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        delay = random.randint(0, 3 * 3600)
        print(f"[Anti-Detection] Scheduled run: 延迟 {delay} 秒...")
        time.sleep(delay)
    
    main()
