# -*- coding: utf-8 -*-
r"""
linovelib_crawler.py

功能：
1. 支持填写小说 ID / 小说详情页链接 / 章节页链接
2. 支持传统分卷页 vol_xxx.html
3. 支持 catalog 目录页
4. 按网页原文顺序解析：文字、图片按出现顺序写入 EPUB
5. 每一卷单独生成一个 EPUB
6. 默认只输出 EPUB；调试时可打开 TXT / MD / JSON 输出

保存路径：
downloads\小说名\epubs\01_卷名.epub

注意：
只爬取公开可访问且你有权保存的内容。
不绕过登录、验证码、付费、反爬或访问限制。
"""

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from ebooklib import epub

try:
    import winreg
except ImportError:
    winreg = None

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc=None):
        return iterable


# =========================================================
# 用户配置区：主要改这里
# =========================================================

ROOT_DIR = Path(__file__).resolve().parent
SAVE_DIR = ROOT_DIR / "downloads"

BASE_URL = "https://www.linovelib.com"

# 填小说 ID / 小说详情页链接 / 章节页链接都可以
# 示例：
# NOVEL_KEYWORD = "5210"
# NOVEL_KEYWORD = "https://www.linovelib.com/novel/5210.html"
# NOVEL_KEYWORD = "https://www.linovelib.com/novel/5210/325384.html"
NOVEL_KEYWORD = "2906"

# 测试阶段建议先只爬 1 卷 2 章
# 确认 EPUB 正常后，想爬完整本，把这两个改成 None
DEFAULT_MAX_VOLUMES = 2
DEFAULT_MAX_CHAPTERS = None

# 请求间隔，建议不要太低
DEFAULT_DELAY = 8

# 只有填写小说名时才用到
DEFAULT_SCAN_PAGES = 5

# 是否下载并嵌入图片到 EPUB
DOWNLOAD_IMAGES = True

# 是否包含网页隐藏的完整插图。该区域通常提示“注意有剧透”，默认不包含。
INCLUDE_SPOILER_IMAGES = False

# 是否保存 TXT。面向普通用户默认只输出 EPUB。
SAVE_TXT = False

# 是否保存每章 MD。面向普通用户默认只输出 EPUB。
SAVE_MD = False

# 是否保存调试用 JSON 文件。面向普通用户默认关闭。
SAVE_JSON = False

# linovelib 的部分章节会先返回乱序正文，再由页面脚本在浏览器里恢复可见顺序。
# 开启后，章节正文会优先读取浏览器渲染后的公开页面 DOM，避免 EPUB 正文顺序错乱。
USE_RENDERED_CHAPTER_DOM = True
RENDERED_DOM_VIRTUAL_TIME_BUDGET_MS = 5000
RENDERED_DOM_TIMEOUT = 45

# EPUB 作者，不填则尝试从网页解析
DEFAULT_AUTHOR = "未知作者"

# EPUB 语言
EPUB_LANGUAGE = "zh-CN"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": BASE_URL + "/",
}


# =========================================================
# 工具函数
# =========================================================

def safe_name(name: str, max_len: int = 80) -> str:
    """清理 Windows 文件名非法字符"""
    if not name:
        return "未命名"

    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .\n\r\t")

    if not name:
        name = "未命名"

    return name[:max_len]


def make_session() -> requests.Session:
    """创建 requests 会话，带重试机制"""
    session = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )

    adapter = HTTPAdapter(max_retries=retry)

    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)

    return session


def guess_image_extension(url: str, content_type: str = "") -> str:
    """判断图片扩展名"""
    path_ext = Path(urlparse(url).path).suffix.lower()

    if path_ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        return path_ext

    content_type = content_type.lower()

    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"

    return ".jpg"


def image_media_type(path: Path) -> str:
    """获取 EPUB 图片 media type"""
    media_type, _ = mimetypes.guess_type(str(path))

    if media_type:
        return media_type

    ext = path.suffix.lower()

    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"

    return "image/jpeg"


def clean_spaces(text: str) -> str:
    """清理空白，但不破坏中文正文"""
    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    return text


def normalize_search_text(text: str) -> str:
    """归一化小说名搜索文本，降低空格和标点差异的影响。"""
    text = html.unescape(text or "")
    text = clean_spaces(text)
    text = text.casefold()
    text = re.sub(r"[《》「」『』【】\[\]（）()〈〉<>·・,，.。:：;；!！?？~～\s]+", "", text)
    return text


def text_to_html_paragraph(text: str) -> str:
    """将一段纯文本转成 EPUB 段落"""
    text = text.strip()
    if not text:
        return ""

    escaped = html.escape(text)
    escaped = escaped.replace("\n", "<br/>")
    return f"<p>{escaped}</p>"


def blocks_to_plain_text(blocks: list) -> str:
    """将按顺序解析的 blocks 转成 TXT 文本"""
    lines = []

    for block in blocks:
        if block["type"] == "text":
            content = block.get("text", "").strip()
            if content:
                lines.append(content)
        elif block["type"] == "image":
            url = block.get("url", "")
            if url:
                lines.append(f"[插图：{url}]")

    return "\n\n".join(lines)


def blocks_to_markdown(blocks: list) -> str:
    """将按顺序解析的 blocks 转成 Markdown"""
    lines = []

    for block in blocks:
        if block["type"] == "text":
            content = block.get("text", "").strip()
            if content:
                lines.append(content)
        elif block["type"] == "image":
            local_path = block.get("local_path")
            url = block.get("url", "")

            if local_path:
                lines.append(f"![插图]({local_path})")
            elif url:
                lines.append(f"![插图]({url})")

    return "\n\n".join(lines)


# =========================================================
# 爬虫主体
# =========================================================

class LinovelibVolumeEpubCrawler:
    def __init__(self, delay: float = 8.0, scan_pages: int = 5):
        self.session = make_session()
        self.delay = delay
        self.scan_pages = scan_pages
        self._rendered_browser_path = None
        self._rendered_browser_checked = False

        # 图片缓存：同一张图只下载一次，但可以在 EPUB 中出现多次
        self.image_cache = {}

    def sleep(self):
        """请求间隔，避免请求过快"""
        if self.delay <= 0:
            return

        jitter = min(0.5, self.delay * 0.25)
        time.sleep(self.delay + random.uniform(0, jitter))

    def get_html(self, url: str) -> str:
        """获取网页 HTML"""
        print(f"\n正在请求页面：{url}")
        self.sleep()

        response = self.session.get(url, timeout=25)
        print(f"页面状态码：{response.status_code}")

        if response.status_code in [403, 429]:
            raise RuntimeError(
                f"访问被限制，HTTP 状态码：{response.status_code}\n"
                f"请降低请求频率，稍后再试。\n"
                f"不要绕过登录、验证码、付费或反爬限制。\n"
                f"URL：{url}"
            )

        response.raise_for_status()

        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"

        return response.text

    def get_soup(self, url: str) -> BeautifulSoup:
        html_text = self.get_html(url)
        return BeautifulSoup(html_text, "html.parser")

    def find_system_browser(self):
        """查找本机可用于导出渲染后 DOM 的 Edge / Chrome。"""
        if self._rendered_browser_checked:
            return self._rendered_browser_path

        local_app_data = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.environ.get("LINOVELIB_BROWSER_PATH", ""),
            shutil.which("msedge"),
            shutil.which("msedge.exe"),
            shutil.which("chrome"),
            shutil.which("chrome.exe"),
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path(local_app_data) / "Microsoft" / "Edge" / "Application" / "msedge.exe") if local_app_data else "",
            str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe") if local_app_data else "",
        ]
        candidates.extend(self.find_browser_paths_from_registry())

        for candidate in candidates:
            if not candidate:
                continue

            path = Path(os.path.expandvars(candidate)).expanduser()
            if path.exists():
                self._rendered_browser_path = str(path)
                break

        self._rendered_browser_checked = True
        return self._rendered_browser_path

    def find_browser_paths_from_registry(self):
        """从 Windows 注册表补充查找 Edge / Chrome 安装路径。"""
        if winreg is None:
            return []

        paths = []
        app_names = ["msedge.exe", "chrome.exe"]
        roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        access_modes = [
            winreg.KEY_READ,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
        ]

        for root in roots:
            for app_name in app_names:
                subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_name}"
                for access in access_modes:
                    try:
                        with winreg.OpenKey(root, subkey, 0, access) as key:
                            value, _ = winreg.QueryValueEx(key, "")
                            if value:
                                paths.append(value)
                    except OSError:
                        continue

        return paths

    def should_use_rendered_chapter_dom(self, soup: BeautifulSoup) -> bool:
        """判断章节页是否需要等待浏览器脚本恢复正文顺序。"""
        if not USE_RENDERED_CHAPTER_DOM:
            return False

        if not soup.select_one("#TextContent"):
            return False

        for script in soup.find_all("script"):
            src = script.get("src", "")
            text = script.get_text("", strip=False)

            if "chapterlog.js" in src or "chapterlog.js" in text:
                return True

        return False

    def websocket_send_frame(self, sock: socket.socket, text: str, opcode: int = 0x1):
        payload = text.encode("utf-8")
        header = bytearray([0x80 | opcode])

        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(payload)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", len(payload)))

        mask = os.urandom(4)
        masked_payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        sock.sendall(bytes(header) + mask + masked_payload)

    def websocket_read_exact(self, sock: socket.socket, size: int) -> bytes:
        chunks = []
        remaining = size

        while remaining > 0:
            chunk = sock.recv(remaining)

            if not chunk:
                raise RuntimeError("浏览器调试连接已关闭。")

            chunks.append(chunk)
            remaining -= len(chunk)

        return b"".join(chunks)

    def websocket_recv_text(self, sock: socket.socket):
        while True:
            first_two = self.websocket_read_exact(sock, 2)
            first, second = first_two[0], first_two[1]
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if length == 126:
                length = struct.unpack("!H", self.websocket_read_exact(sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.websocket_read_exact(sock, 8))[0]

            mask = self.websocket_read_exact(sock, 4) if masked else b""
            payload = self.websocket_read_exact(sock, length) if length else b""

            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x1:
                return payload.decode("utf-8", errors="replace")

            if opcode == 0x8:
                return None

            if opcode == 0x9:
                self.websocket_send_frame(sock, payload.decode("utf-8", errors="replace"), opcode=0xA)

    def websocket_connect(self, ws_url: str, timeout: int = 10) -> socket.socket:
        parsed = urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"

        if parsed.query:
            path += "?" + parsed.query

        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            sock.close()
            raise RuntimeError("浏览器调试 WebSocket 握手失败。")

        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")

        if f"Sec-WebSocket-Accept: {accept}".lower() not in response.decode(
            "latin1",
            errors="ignore",
        ).lower():
            sock.close()
            raise RuntimeError("浏览器调试 WebSocket 校验失败。")

        return sock

    def cdp_call(self, sock: socket.socket, message_id: int, method: str, params=None):
        payload = {
            "id": message_id,
            "method": method,
            "params": params or {},
        }
        self.websocket_send_frame(sock, json.dumps(payload, ensure_ascii=False))

        while True:
            message = self.websocket_recv_text(sock)
            if message is None:
                raise RuntimeError("浏览器调试连接已关闭。")

            data = json.loads(message)

            if data.get("id") != message_id:
                continue

            if "error" in data:
                raise RuntimeError(data["error"].get("message", "浏览器调试调用失败。"))

            return data.get("result", {})

    def get_free_debug_port(self) -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]
        finally:
            probe.close()

    def collect_browser_stderr(self, process: subprocess.Popen) -> str:
        if not process or not process.stderr:
            return ""

        try:
            _, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            return ""

        if not stderr:
            return ""

        if isinstance(stderr, bytes):
            return stderr.decode("utf-8", errors="replace").strip()

        return str(stderr).strip()

    def wait_for_browser_debug_port(self, process: subprocess.Popen, port: int, timeout: int = 12):
        deadline = time.time() + timeout
        last_error = ""

        while time.time() < deadline:
            if process.poll() is not None:
                error_text = self.collect_browser_stderr(process)
                if error_text:
                    return False, error_text
                return False, f"浏览器进程提前退出，退出码：{process.returncode}"

            try:
                with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
                    return True, ""
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.2)

        return False, f"浏览器调试端口启动超时：{last_error}"

    def terminate_browser_process(self, process: subprocess.Popen):
        if not process or process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def launch_render_browser(self, browser_path: str, temp_profile: str):
        last_error = ""

        for headless_arg in ["--headless=new", "--headless"]:
            port = self.get_free_debug_port()
            command = [
                browser_path,
                headless_arg,
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-software-rasterizer",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-allow-origins=*",
                f"--user-data-dir={temp_profile}",
                f"--remote-debugging-port={port}",
                f"--user-agent={HEADERS['User-Agent']}",
                "about:blank",
            ]

            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            ok, error_text = self.wait_for_browser_debug_port(process, port)
            if ok:
                return process, port, ""

            last_error = error_text or f"{headless_arg} 启动失败"
            self.terminate_browser_process(process)

        return None, None, last_error

    def get_rendered_soup(self, url: str):
        """
        使用本机浏览器导出脚本执行后的 DOM。
        这不绕过访问限制，只是让公开页面完成自身的正文排序脚本。
        """
        browser_path = self.find_system_browser()
        if not browser_path:
            print("未找到 Edge / Chrome，已退回原始 HTML 正文解析。")
            return None

        temp_profile = tempfile.mkdtemp(prefix="linovelib-render-")
        process = None
        sock = None

        try:
            print("正在读取浏览器渲染后的章节正文顺序...")
            process, port, launch_error = self.launch_render_browser(browser_path, temp_profile)

            if not process or not port:
                print(f"浏览器启动失败，已退回原始 HTML。原因：{launch_error}")
                return None

            target_request = Request(
                f"http://127.0.0.1:{port}/json/new?about:blank",
                method="PUT",
            )
            with urlopen(target_request, timeout=5) as response:
                target_info = json.loads(response.read().decode("utf-8"))

            ws_url = target_info.get("webSocketDebuggerUrl")
            if not ws_url:
                print("浏览器页面调试地址获取失败，已退回原始 HTML。")
                return None

            sock = self.websocket_connect(ws_url, timeout=10)
            message_id = 1

            self.cdp_call(sock, message_id, "Page.enable")
            message_id += 1
            self.cdp_call(sock, message_id, "Runtime.enable")
            message_id += 1
            self.cdp_call(sock, message_id, "Page.navigate", {"url": url})
            message_id += 1

            ready_expression = """
(() => {
  const container = document.querySelector('#TextContent');
  if (!container) return false;
  const hasTaggedParagraph = container.querySelector('p[data-k]');
  let hasHiddenRule = false;
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      for (const rule of Array.from(sheet.cssRules || [])) {
        const text = rule.cssText || '';
        if (text.includes('data-k') && (text.includes('scale(0)') || text.includes('position: absolute'))) {
          hasHiddenRule = true;
          break;
        }
      }
    } catch (error) {}
    if (hasHiddenRule) break;
  }
  return document.readyState === 'complete' && Boolean(hasTaggedParagraph || hasHiddenRule);
})()
"""
            deadline = time.time() + max(8, RENDERED_DOM_VIRTUAL_TIME_BUDGET_MS / 1000)

            while time.time() < deadline:
                result = self.cdp_call(
                    sock,
                    message_id,
                    "Runtime.evaluate",
                    {
                        "expression": ready_expression,
                        "returnByValue": True,
                    },
                )
                message_id += 1

                if result.get("result", {}).get("value"):
                    break

                time.sleep(0.25)

            extract_expression = f"""
(() => {{
  const includeSpoilerImages = {str(INCLUDE_SPOILER_IMAGES).lower()};
  const container = document.querySelector('#TextContent');
  if (!container) return '';

  if (includeSpoilerImages) {{
    const hiddenImages = document.getElementById('hidden-images');
    if (hiddenImages) hiddenImages.style.display = 'block';
  }}

  const hiddenAttrs = new Set();
  for (const sheet of Array.from(document.styleSheets)) {{
    try {{
      for (const rule of Array.from(sheet.cssRules || [])) {{
        const text = rule.cssText || '';
        const match = text.match(/p\\[(data-k\\d+)\\]/i);
        if (match && (/scale\\(0\\)/i.test(text) || /position\\s*:\\s*absolute/i.test(text))) {{
          hiddenAttrs.add(match[1].toLowerCase());
        }}
      }}
    }} catch (error) {{}}
  }}

  function isHiddenElement(element) {{
    if (!element || element.nodeType !== 1) return false;
    const style = getComputedStyle(element);
    const transform = style.transform || '';
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return true;
    if (/matrix\\(0(?:,|\\s)/.test(transform) || /scale\\(0\\)/.test(transform)) return true;
    for (const attr of Array.from(element.getAttributeNames())) {{
      if (hiddenAttrs.has(attr.toLowerCase())) return true;
    }}
    return false;
  }}

  const clone = container.cloneNode(false);
  for (const child of Array.from(container.childNodes)) {{
    if (child.nodeType === 1 && isHiddenElement(child)) continue;
    clone.appendChild(child.cloneNode(true));
  }}

  return clone.outerHTML;
}})()
"""
            result = self.cdp_call(
                sock,
                message_id,
                "Runtime.evaluate",
                {
                    "expression": extract_expression,
                    "returnByValue": True,
                },
            )
            html_text = result.get("result", {}).get("value", "")

            if not html_text:
                print("渲染后 DOM 未找到正文容器，已退回原始 HTML。")
                return None

            rendered_soup = BeautifulSoup(html_text, "html.parser")
            return rendered_soup
        except Exception as exc:
            print(f"渲染后 DOM 读取异常，已退回原始 HTML：{exc}")
            return None
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

            shutil.rmtree(temp_profile, ignore_errors=True)

    def remove_rendered_hidden_paragraphs(self, soup: BeautifulSoup):
        """
        移除网站脚本注入的不可见诱饵段落。
        这些段落通常会被 CSS 标记为 position:absolute + transform:scale(0)。
        """
        hidden_attrs = set()

        for style in soup.find_all("style"):
            css_text = style.get_text("\n", strip=False)

            for match in re.finditer(
                r"p\[(data-k\d+)\]\s*\{[^}]*?(?:transform\s*:\s*scale\(0\)|position\s*:\s*absolute)",
                css_text,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                hidden_attrs.add(match.group(1).lower())

        if not hidden_attrs:
            return

        container = soup.select_one("#TextContent")
        if not container:
            return

        removed = 0
        for paragraph in list(container.find_all("p")):
            attr_names = {name.lower() for name in paragraph.attrs}

            if attr_names.intersection(hidden_attrs):
                paragraph.decompose()
                removed += 1

        if removed:
            print(f"已过滤网页隐藏诱饵段落：{removed} 段")

    # =====================================================
    # 第一步：根据小说名 / 小说 ID / 链接找到详情页
    # =====================================================

    def resolve_book_url(self, keyword: str) -> str:
        """
        支持：
        1. 小说 ID：5210
        2. 小说详情页：https://www.linovelib.com/novel/5210.html
        3. 章节页：https://www.linovelib.com/novel/5210/325384.html
        4. 小说名：不推荐，可能触发 403
        """
        keyword = keyword.strip()

        if not keyword:
            raise RuntimeError("请先在 NOVEL_KEYWORD 中填写小说名、小说ID或小说详情页链接。")

        if keyword.startswith("http://") or keyword.startswith("https://"):
            match = re.search(r"/novel/(\d+)", keyword)

            if match:
                book_id = match.group(1)
                return f"{BASE_URL}/novel/{book_id}.html"

            return keyword

        if keyword.isdigit():
            return f"{BASE_URL}/novel/{keyword}.html"

        return self.search_book_by_name(keyword)

    def search_book_by_name(self, keyword: str) -> str:
        """
        按小说名称搜索。
        优先扫描首页推荐区，再扫描文库页；精确匹配优先，找不到再使用包含匹配。
        """
        print(f"正在搜索小说：{keyword}")
        print("会先扫描首页，再扫描文库页。")

        matches = []
        seen = set()
        keyword_norm = normalize_search_text(keyword)

        if not keyword_norm:
            raise RuntimeError("小说名称不能为空。")

        def add_matches_from_soup(soup: BeautifulSoup, source_name: str):
            exact_added = []

            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                title = clean_spaces(a.get_text(" ", strip=True))

                if not title:
                    continue

                if not re.search(r"/novel/\d+\.html$", href):
                    continue

                full_url = urljoin(BASE_URL, href)

                if full_url in seen:
                    continue

                title_norm = normalize_search_text(title)

                if not title_norm:
                    continue

                if title_norm == keyword_norm:
                    score = 100
                elif keyword_norm in title_norm:
                    score = 80
                elif title_norm in keyword_norm:
                    score = 70
                else:
                    continue

                seen.add(full_url)
                item = {
                    "title": title,
                    "url": full_url,
                    "score": score,
                    "source": source_name,
                }
                matches.append(item)

                if score == 100:
                    exact_added.append(item)

            return exact_added

        print(f"扫描首页：{BASE_URL}/")

        try:
            home_soup = self.get_soup(BASE_URL + "/")
            exact_matches = add_matches_from_soup(home_soup, "首页")
            if exact_matches:
                chosen = exact_matches[0]
                print(f"首页找到精确匹配：{chosen['title']}")
                print(f"详情页：{chosen['url']}")
                return chosen["url"]
        except Exception as e:
            print(f"首页读取失败：{e}")

        print(f"将扫描文库前 {self.scan_pages} 页。")

        for page in range(1, self.scan_pages + 1):
            if page == 1:
                url = f"{BASE_URL}/wenku/"
            else:
                url = f"{BASE_URL}/wenku/lastupdate_0_0_0_0_0_0_0_{page}_0.html"

            print(f"扫描第 {page} 页：{url}")

            try:
                soup = self.get_soup(url)
            except Exception as e:
                print(f"第 {page} 页读取失败：{e}")
                continue

            exact_matches = add_matches_from_soup(soup, f"文库第 {page} 页")
            if exact_matches:
                chosen = exact_matches[0]
                print(f"文库第 {page} 页找到精确匹配：{chosen['title']}")
                print(f"详情页：{chosen['url']}")
                return chosen["url"]

        if not matches:
            raise RuntimeError(
                f"\n没有找到小说：{keyword}\n\n"
                f"建议：\n"
                f"1. 优先填写小说 ID。\n"
                f"2. 也可以填写小说详情页链接。\n"
                f"3. 可以适当调大搜索页数，但太大容易 403。\n"
            )

        matches.sort(key=lambda item: item["score"], reverse=True)
        chosen = matches[0]

        print("\n找到以下匹配结果：")
        for index, item in enumerate(matches[:10], 1):
            print(f"{index}. {item['title']}（{item['source']}，匹配度 {item['score']}）")
            print(f"   {item['url']}")

        print(f"\n默认选择：{chosen['title']}")
        print(f"详情页：{chosen['url']}")

        return chosen["url"]

    # =====================================================
    # 第二步：解析小说详情页
    # =====================================================

    def parse_author(self, soup: BeautifulSoup) -> str:
        """尝试解析作者"""
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")

            if "author" in href:
                text = a.get_text(" ", strip=True)
                if text:
                    return text

        text = soup.get_text("\n", strip=True)
        match = re.search(r"作者[:：]\s*([^\n\r]+)", text)

        if match:
            author = match.group(1).strip()
            author = re.sub(r"\s+", " ", author)
            return author[:50]

        return DEFAULT_AUTHOR

    def parse_cover_url(self, soup: BeautifulSoup):
        """尝试解析封面图"""
        selectors = [
            ".book-img img",
            ".bookimg img",
            "#bookimg img",
            ".book-cover img",
            ".cover img",
            "img",
        ]

        for selector in selectors:
            img = soup.select_one(selector)

            if not img:
                continue

            src = (
                img.get("data-src")
                or img.get("data-original")
                or img.get("data-url")
                or img.get("src")
            )

            if not src:
                continue

            lower_src = src.lower()

            if any(word in lower_src for word in ["logo", "avatar", "icon", "loading", "blank"]):
                continue

            return urljoin(BASE_URL, src)

        return None

    def parse_book_info(self, book_url: str) -> dict:
        """
        解析小说详情页：
        1. 优先找传统 vol_xxx.html 分卷
        2. 如果没有分卷，则自动使用 /catalog 目录页
        """
        soup = self.get_soup(book_url)

        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else "未知小说"

        book_id_match = re.search(r"/novel/(\d+)", book_url)

        if not book_id_match:
            raise RuntimeError(f"无法从小说 URL 中提取小说 ID：{book_url}")

        book_id = book_id_match.group(1)
        author = self.parse_author(soup)
        cover_url = self.parse_cover_url(soup)

        volume_links = []
        seen = set()

        # 第一种结构：传统分卷页 /novel/8/vol_1840.html
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")

            if not re.search(rf"/novel/{book_id}/vol_\d+\.html$", href):
                continue

            full_url = urljoin(BASE_URL, href)

            if full_url in seen:
                continue

            seen.add(full_url)

            volume_title = a.get_text(" ", strip=True)
            if not volume_title:
                volume_title = f"分卷_{len(volume_links) + 1}"

            volume_links.append(
                {
                    "title": volume_title,
                    "url": full_url,
                    "type": "volume",
                }
            )

        # 如果详情页有传统分卷链接，就用分卷链接
        if volume_links:
            volume_links.reverse()

            return {
                "book_id": book_id,
                "title": title,
                "author": author,
                "cover_url": cover_url,
                "url": book_url,
                "volumes": volume_links,
            }

        # 第二种结构：没有传统分卷，使用 /catalog
        catalog_url = f"{BASE_URL}/novel/{book_id}/catalog"

        print("没有找到传统分卷链接，尝试使用目录页：")
        print(catalog_url)

        # 这里先放一个 catalog 入口，后面会尝试按 catalog 内部标题拆卷
        volume_links.append(
            {
                "title": title,
                "url": catalog_url,
                "type": "catalog",
            }
        )

        return {
            "book_id": book_id,
            "title": title,
            "author": author,
            "cover_url": cover_url,
            "url": book_url,
            "volumes": volume_links,
        }

    # =====================================================
    # 第三步：解析分卷页 / catalog 目录页
    # =====================================================

    def parse_volume_info(self, volume_url: str, book_id: str) -> dict:
        """
        支持：
        1. /vol_xxx.html 分卷页
        2. /catalog 目录页
        """
        soup = self.get_soup(volume_url)

        h1 = soup.find("h1")
        volume_title = h1.get_text(strip=True) if h1 else "未知分卷"

        h2 = soup.find("h2")
        if h2 and h2.get_text(strip=True):
            volume_title = h2.get_text(" ", strip=True)

        chapters = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            chapter_title = a.get_text(" ", strip=True)

            # 匹配章节链接：/novel/5210/325384.html
            if not re.search(rf"/novel/{book_id}/\d+\.html$", href):
                continue

            full_url = urljoin(BASE_URL, href)

            if full_url in seen:
                continue

            seen.add(full_url)

            if not chapter_title:
                chapter_title = f"章节_{len(chapters) + 1}"

            chapters.append(
                {
                    "title": chapter_title,
                    "url": full_url,
                }
            )

        if not chapters:
            print(f"警告：该页面没有解析到章节：{volume_url}")

        return {
            "title": volume_title,
            "url": volume_url,
            "chapters": chapters,
        }

    def parse_catalog_as_volumes(self, catalog_url: str, book_id: str, fallback_title: str) -> list:
        """
        对没有传统 vol_xxx.html 的 catalog 页面，尝试按页面中的 h2/h3/h4 拆成多个卷。
        如果 catalog 本身只有一组章节，就生成一个卷。
        """
        soup = self.get_soup(catalog_url)

        groups = []
        current_group = {
            "title": fallback_title,
            "url": catalog_url,
            "chapters": [],
        }

        seen_chapters = set()

        # 顺序扫描标题和章节链接
        elements = soup.find_all(["h2", "h3", "h4", "a"])

        for elem in elements:
            if elem.name in ["h2", "h3", "h4"]:
                title = elem.get_text(" ", strip=True)

                if not title:
                    continue

                # 如果当前组已经有章节，遇到新标题就开新卷
                if current_group["chapters"]:
                    groups.append(current_group)
                    current_group = {
                        "title": title,
                        "url": catalog_url,
                        "chapters": [],
                    }
                else:
                    current_group["title"] = title

            elif elem.name == "a":
                href = elem.get("href", "")
                chapter_title = elem.get_text(" ", strip=True)

                if not re.search(rf"/novel/{book_id}/\d+\.html$", href):
                    continue

                full_url = urljoin(BASE_URL, href)

                if full_url in seen_chapters:
                    continue

                seen_chapters.add(full_url)

                if not chapter_title:
                    chapter_title = f"章节_{len(current_group['chapters']) + 1}"

                current_group["chapters"].append(
                    {
                        "title": chapter_title,
                        "url": full_url,
                    }
                )

        if current_group["chapters"]:
            groups.append(current_group)

        if not groups:
            print("catalog 页面没有解析到章节，将返回空列表。")
            return []

        return groups

    def build_volume_list(self, book_info: dict) -> list:
        """
        统一构造卷列表：
        - 传统分卷：每个 vol_xxx.html 是一卷
        - catalog：尝试按 catalog 内部标题拆卷
        """
        volumes = book_info["volumes"]

        if len(volumes) == 1 and volumes[0].get("type") == "catalog":
            print("\n检测到 catalog 目录结构，尝试按目录页拆卷...")
            groups = self.parse_catalog_as_volumes(
                catalog_url=volumes[0]["url"],
                book_id=book_info["book_id"],
                fallback_title=book_info["title"],
            )

            if groups:
                print(f"catalog 拆分得到 {len(groups)} 个卷/目录组。")
                return groups

        final_volumes = []

        for volume in volumes:
            info = self.parse_volume_info(
                volume["url"],
                book_info["book_id"],
            )
            final_volumes.append(info)

        return final_volumes

    # =====================================================
    # 第四步：按原文顺序解析章节正文和图片
    # =====================================================

    def find_content_container(self, soup: BeautifulSoup):
        """
        尝试找到正文容器。
        只有找到真正正文容器，才能尽量避免导航、推荐、广告等干扰。
        """
        selectors = [
            "#TextContent",
            "#chaptercontent",
            "#chapter_content",
            "#content",
            "#mlfy_main_text",
            ".mlfy_main_text",
            ".acontent",
            ".read-content",
            ".chapter-content",
            ".article-content",
            ".chapter",
            "article",
        ]

        for selector in selectors:
            node = soup.select_one(selector)

            if node and (node.get_text(strip=True) or node.find("img")):
                return node

        return soup.body or soup

    def extract_chapter_title(self, soup: BeautifulSoup, default_title: str) -> str:
        h1 = soup.find("h1")

        if h1:
            title = h1.get_text(" ", strip=True)
            if title:
                return title

        return default_title

    def should_skip_text(self, text: str) -> bool:
        """判断一段文字是不是导航或无关文字"""
        text = clean_spaces(text)

        if not text:
            return True

        normalized_html_text = (
            html.unescape(text)
            .replace("“", "\"")
            .replace("”", "\"")
            .replace("‘", "'")
            .replace("’", "'")
            .strip()
            .lower()
        )

        # 有些页面会把广告/占位节点以文本形式塞进正文，例如：
        # <div class="dag"></div>。这类内容不是小说正文，生成 EPUB 前过滤掉。
        if re.fullmatch(r"(?:<[^>]+>\s*)+", normalized_html_text):
            return True

        if re.search(
            r"<\s*div\b[^>]*(?:class|id)\s*=\s*['\"]?dag['\"]?[^>]*>",
            normalized_html_text,
        ):
            return True

        remove_exact = {
            "翻上页",
            "翻下页",
            "上一页",
            "下一页",
            "上一章",
            "下一章",
            "目录",
            "返回目录",
            "返回书页",
            "加入书架",
            "推荐本书",
            "呼出功能",
            "漫画＆插图",
            "漫画&插图",
            "书页",
            "繁體化",
            "首页",
            "分类",
            "文库",
            "排行",
            "全本",
            "登录",
            "注册",
            "+书签",
            "点赞",
        }

        remove_contains = [
            "哔哩轻小说",
            "www.linovelib.com",
            "内容加载失败",
            "內容加載失敗",
            "请检查网络",
            "請檢查網絡",
            "建议使用上下翻页",
            "建議使用上下翻頁",
            "最新网址",
            "全部小说",
            "热门小说",
            "本站所有小说",
            "if you see this",
            "javascript",
        ]

        if text in remove_exact:
            return True

        if any(word in text for word in remove_contains):
            return True

        return False

    def is_bad_tag(self, tag: Tag) -> bool:
        """判断是否是不需要解析的标签"""
        if not isinstance(tag, Tag):
            return False

        if tag.name in ["script", "style", "nav", "header", "footer", "button", "form", "iframe"]:
            return True

        class_text = " ".join(tag.get("class", [])).lower()
        id_text = str(tag.get("id", "")).lower()
        role_text = str(tag.get("role", "")).lower()

        if id_text == "show-more-images":
            return True

        if id_text == "hidden-images" and not INCLUDE_SPOILER_IMAGES:
            return True

        bad_words = [
            "nav",
            "menu",
            "header",
            "footer",
            "comment",
            "review",
            "recommend",
            "ads",
            "ad-",
            "share",
            "breadcrumb",
            "pager",
            "pagebar",
            "operate",
            "toolbar",
        ]

        joined = f"{class_text} {id_text} {role_text}"

        if any(word in joined for word in bad_words):
            return True

        return False

    def is_valid_image_src(self, src: str) -> bool:
        """过滤无效图片"""
        if not src:
            return False

        src = src.strip()

        if not src:
            return False

        lower_src = src.lower()

        if lower_src.startswith("data:"):
            return False

        bad_words = [
            "loading",
            "sloading",
            "logo",
            "avatar",
            "default",
            "blank",
            "spacer",
            "icon",
            "button",
        ]

        if any(word in lower_src for word in bad_words):
            return False

        return True

    def get_image_src(self, img: Tag):
        """从 img 标签里提取图片地址"""
        src = (
            img.get("data-src")
            or img.get("data-original")
            or img.get("data-url")
            or img.get("data-lazy-src")
            or img.get("src")
        )

        if not src:
            srcset = img.get("srcset")
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]

        if not self.is_valid_image_src(src):
            return None

        return urljoin(BASE_URL, src)

    def merge_text_block(self, blocks: list, text: str):
        """
        合并连续文字，避免 EPUB 里一行一个碎段。
        """
        text = clean_spaces(text)

        if self.should_skip_text(text):
            return

        if blocks and blocks[-1]["type"] == "text":
            old_text = blocks[-1]["text"].strip()

            if old_text:
                blocks[-1]["text"] = old_text + "\n" + text
            else:
                blocks[-1]["text"] = text
        else:
            blocks.append(
                {
                    "type": "text",
                    "text": text,
                }
            )

    def append_image_block(self, blocks: list, img: Tag):
        """按当前位置追加图片 block。"""
        img_url = self.get_image_src(img)

        if not img_url:
            return

        blocks.append(
            {
                "type": "image",
                "url": img_url,
                "alt": clean_spaces(img.get("alt", "")),
            }
        )

    def split_text_and_inline_images(self, node: Tag, page_url: str) -> list:
        """
        在一个段落/块级节点内部按子节点顺序拆分文字和图片。
        这样图片前后的文字不会被合并到错误位置。
        """
        child_blocks = []

        def walk_inline(child):
            if isinstance(child, NavigableString):
                self.merge_text_block(child_blocks, str(child))
                return

            if not isinstance(child, Tag):
                return

            if self.is_bad_tag(child):
                return

            if child.name == "img":
                self.append_image_block(child_blocks, child)
                return

            if child.name == "br":
                if child_blocks and child_blocks[-1]["type"] == "text":
                    child_blocks[-1]["text"] = child_blocks[-1]["text"].rstrip() + "\n"
                return

            for grand_child in list(child.children):
                walk_inline(grand_child)

        for child in list(node.children):
            walk_inline(child)

        return child_blocks

    def append_block_preserving_boundaries(self, blocks: list, block: dict):
        """追加解析结果，避免跨图片或跨段落把文字粘到一起。"""
        if block.get("type") == "text":
            text = clean_spaces(block.get("text", ""))

            if self.should_skip_text(text):
                return

            if blocks and blocks[-1]["type"] == "text":
                blocks[-1]["text"] = blocks[-1]["text"].rstrip() + "\n" + text
            else:
                blocks.append({"type": "text", "text": text})

        elif block.get("type") == "image":
            blocks.append(block)

    def extract_ordered_blocks(self, container: Tag, page_url: str) -> list:
        """
        核心函数：
        按网页 DOM 顺序提取内容。
        遇到文字，生成 text block。
        遇到图片，生成 image block。
        """
        blocks = []
        block_tags = {
            "p",
            "div",
            "section",
            "article",
            "li",
            "blockquote",
            "h2",
            "h3",
            "h4",
        }

        def walk(node):
            if isinstance(node, NavigableString):
                text = clean_spaces(str(node))
                self.merge_text_block(blocks, text)
                return

            if not isinstance(node, Tag):
                return

            if self.is_bad_tag(node):
                return

            if node.name == "img":
                self.append_image_block(blocks, node)
                return

            if node.name == "br":
                if blocks and blocks[-1]["type"] == "text":
                    blocks[-1]["text"] = blocks[-1]["text"].rstrip() + "\n"
                return

            if node.name in block_tags and node is not container:
                child_blocks = self.split_text_and_inline_images(node, page_url)

                for child_block in child_blocks:
                    self.append_block_preserving_boundaries(blocks, child_block)

                return

            for child in list(node.children):
                walk(child)

        walk(container)

        cleaned = []
        previous_image_url = None

        for block in blocks:
            if block["type"] == "text":
                text = block.get("text", "").strip()
                if text and not self.should_skip_text(text):
                    cleaned.append(
                        {
                            "type": "text",
                            "text": text,
                        }
                    )
                previous_image_url = None

            elif block["type"] == "image":
                url = block.get("url", "").strip()
                if not url:
                    continue

                # 只过滤连续重复图片，不做全局去重，避免破坏原文位置
                if previous_image_url == url:
                    continue

                cleaned.append(block)
                previous_image_url = url

        return cleaned

    # =====================================================
    # 第五步：下载图片
    # =====================================================

    def download_binary(self, url: str, referer: str = None):
        """下载二进制内容，用于图片和封面"""
        try:
            print(f"正在下载资源：{url}")
            self.sleep()

            headers = dict(HEADERS)
            if referer:
                headers["Referer"] = referer

            response = self.session.get(
                url,
                timeout=30,
                stream=True,
                headers=headers,
            )

            print(f"资源状态码：{response.status_code}")

            if response.status_code in [403, 429]:
                print(f"资源访问受限，已跳过：{url}")
                return None, ""

            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            data = response.content

            return data, content_type

        except Exception as e:
            print(f"资源下载失败：{url}")
            print(f"原因：{e}")
            return None, ""

    def download_image_to_file(
        self,
        img_url: str,
        image_dir: Path,
        volume_order: int,
        chapter_order: int,
        image_order: int,
        referer: str = None,
    ):
        """
        下载图片到本地，并返回图片信息。
        同一 URL 会复用缓存，不重复下载。
        """
        if img_url in self.image_cache:
            return self.image_cache[img_url]

        data, content_type = self.download_binary(img_url, referer=referer)

        if not data:
            return None

        ext = guess_image_extension(img_url, content_type)
        filename = f"v{volume_order:03d}_ch{chapter_order:04d}_img{image_order:03d}{ext}"
        save_path = image_dir / filename

        image_dir.mkdir(parents=True, exist_ok=True)

        with open(save_path, "wb") as f:
            f.write(data)

        info = {
            "url": img_url,
            "local_path": str(save_path),
            "filename": filename,
            "epub_href": f"images/{filename}",
            "media_type": image_media_type(save_path),
        }

        self.image_cache[img_url] = info
        return info

    # =====================================================
    # 第六步：解析单章
    # =====================================================

    def parse_chapter(
        self,
        chapter: dict,
        chapter_dir: Path,
        image_dir: Path,
        volume_order: int,
        chapter_order: int,
    ) -> dict:
        """
        解析单个章节：
        只解析当前章节 URL，不追踪“下一页”。
        重点：按原网页顺序解析文字和图片。
        """
        chapter_url = chapter["url"]
        default_title = chapter["title"]

        print(f"\n正在解析章节：{default_title}")
        print(f"章节地址：{chapter_url}")

        soup = self.get_soup(chapter_url)

        chapter_real_title = self.extract_chapter_title(soup, default_title)

        needs_rendered_dom = self.should_use_rendered_chapter_dom(soup)

        if needs_rendered_dom:
            rendered_soup = self.get_rendered_soup(chapter_url)

            if rendered_soup:
                soup = rendered_soup
                print("已使用浏览器渲染后的可见正文顺序。")
            else:
                self.remove_rendered_hidden_paragraphs(soup)
                print("警告：本章需要浏览器渲染才能完全还原网页正文顺序，当前已使用原始 HTML 兜底，文本可能仍与网页显示不一致。")

        container = self.find_content_container(soup)

        blocks = self.extract_ordered_blocks(container, chapter_url)

        text_count = sum(1 for b in blocks if b["type"] == "text")
        image_count = sum(1 for b in blocks if b["type"] == "image")

        print(f"章节标题：{chapter_real_title}")
        print(f"文字块数量：{text_count}")
        print(f"图片块数量：{image_count}")

        if DOWNLOAD_IMAGES:
            current_image_order = 0

            for block in blocks:
                if block["type"] != "image":
                    continue

                current_image_order += 1
                img_url = block.get("url")

                print(f"准备下载第 {current_image_order}/{image_count} 张图片")

                image_info = self.download_image_to_file(
                    img_url=img_url,
                    image_dir=image_dir,
                    volume_order=volume_order,
                    chapter_order=chapter_order,
                    image_order=current_image_order,
                    referer=chapter_url,
                )

                if image_info:
                    block.update(image_info)
        else:
            for block in blocks:
                if block["type"] == "image":
                    block["local_path"] = ""
                    block["filename"] = ""
                    block["epub_href"] = ""
                    block["media_type"] = ""

        return {
            "title": chapter_real_title,
            "url": chapter_url,
            "blocks": blocks,
            "text": blocks_to_plain_text(blocks),
        }

    # =====================================================
    # 第七步：生成单卷 EPUB
    # =====================================================

    def render_blocks_to_epub_html(self, blocks: list) -> str:
        """
        将顺序 blocks 渲染成 EPUB HTML。
        这里不改变 block 顺序，因此图片位置会和解析出的原文顺序一致。
        """
        html_parts = []

        for block in blocks:
            if block["type"] == "text":
                text = block.get("text", "").strip()

                if not text:
                    continue

                paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

                for paragraph in paragraphs:
                    html_parts.append(text_to_html_paragraph(paragraph))

            elif block["type"] == "image":
                epub_href = block.get("epub_href", "")
                img_url = block.get("url", "")
                alt = html.escape(block.get("alt", "") or "插图")

                if epub_href:
                    html_parts.append(
                        f'<div class="image-block">'
                        f'<img src="{html.escape(epub_href)}" alt="{alt}"/>'
                        f'</div>'
                    )
                elif img_url:
                    html_parts.append(
                        f'<p class="center">插图链接：{html.escape(img_url)}</p>'
                    )

        return "\n".join(html_parts)

    def add_cover_to_book(self, book: epub.EpubBook, book_info: dict):
        """给 EPUB 添加封面"""
        cover_url = book_info.get("cover_url")
        if not cover_url:
            return

        cover_data, cover_content_type = self.download_binary(
            cover_url,
            referer=book_info["url"],
        )

        if not cover_data:
            return

        cover_ext = guess_image_extension(cover_url, cover_content_type)
        cover_name = f"cover{cover_ext}"
        book.set_cover(cover_name, cover_data)

    def create_epub_for_volume(
        self,
        book_info: dict,
        volume_result: dict,
        volume_index: int,
        epubs_dir: Path,
    ) -> Path:
        """
        给单独一卷生成一个 EPUB。
        """
        book_title = safe_name(book_info["title"], 60)
        volume_title = safe_name(volume_result["title"], 80)

        epub_filename = f"{volume_index:02d}_{volume_title}.epub"
        epub_path = epubs_dir / epub_filename

        epub_book = epub.EpubBook()

        epub_book.set_identifier(str(uuid.uuid4()))
        epub_book.set_title(f"{book_info['title']} - {volume_result['title']}")
        epub_book.set_language(EPUB_LANGUAGE)
        epub_book.add_author(book_info.get("author") or DEFAULT_AUTHOR)

        css = """
        body {
            font-family: "Microsoft YaHei", "SimSun", serif;
            line-height: 1.8;
            font-size: 1em;
            margin: 0;
            padding: 0;
        }
        h1, h2, h3 {
            text-align: center;
            line-height: 1.5;
            margin-top: 1.5em;
            margin-bottom: 1em;
        }
        p {
            text-indent: 2em;
            margin: 0.6em 0;
        }
        .center {
            text-align: center;
            text-indent: 0;
        }
        .image-block {
            text-align: center;
            margin: 1em 0;
            text-indent: 0;
            page-break-inside: avoid;
        }
        img {
            max-width: 100%;
            height: auto;
        }
        """

        style_item = epub.EpubItem(
            uid="style_default",
            file_name="style/default.css",
            media_type="text/css",
            content=css.encode("utf-8"),
        )

        epub_book.add_item(style_item)

        # 封面
        self.add_cover_to_book(epub_book, book_info)

        # 添加这一卷用到的图片
        added_images = set()

        for chapter in volume_result["chapters"]:
            for block in chapter.get("blocks", []):
                if block.get("type") != "image":
                    continue

                local_path = block.get("local_path")
                epub_href = block.get("epub_href")

                if not local_path or not epub_href:
                    continue

                path = Path(local_path)

                if not path.exists():
                    continue

                if epub_href in added_images:
                    continue

                with open(path, "rb") as f:
                    image_content = f.read()

                image_item = epub.EpubItem(
                    uid=f"img_{len(added_images) + 1}",
                    file_name=epub_href,
                    media_type=block.get("media_type") or image_media_type(path),
                    content=image_content,
                )

                epub_book.add_item(image_item)
                added_images.add(epub_href)

        spine = ["nav"]
        toc = []

        # 单卷标题页
        intro_html = f"""
        <html xmlns="http://www.w3.org/1999/xhtml">
        <head>
            <title>{html.escape(book_info["title"])} - {html.escape(volume_result["title"])}</title>
            <link rel="stylesheet" type="text/css" href="style/default.css"/>
        </head>
        <body>
            <h1>{html.escape(book_info["title"])}</h1>
            <h2>{html.escape(volume_result["title"])}</h2>
            <p class="center">作者：{html.escape(book_info.get("author") or DEFAULT_AUTHOR)}</p>
            <p class="center">来源：{html.escape(book_info.get("url", ""))}</p>
        </body>
        </html>
        """

        intro = epub.EpubHtml(
            title="书籍信息",
            file_name="intro.xhtml",
            lang=EPUB_LANGUAGE,
        )
        intro.content = intro_html
        intro.add_item(style_item)

        epub_book.add_item(intro)
        spine.append(intro)
        toc.append(intro)

        chapter_items = []

        for chapter_index, chapter in enumerate(volume_result["chapters"], 1):
            chapter_title = chapter["title"]
            chapter_file_name = f"chapter_{chapter_index:04d}.xhtml"

            ordered_content_html = self.render_blocks_to_epub_html(
                chapter.get("blocks", [])
            )

            if not ordered_content_html.strip():
                ordered_content_html = "<p>本章未提取到正文内容。</p>"

            chapter_html = f"""
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
                <title>{html.escape(chapter_title)}</title>
                <link rel="stylesheet" type="text/css" href="style/default.css"/>
            </head>
            <body>
                <h2>{html.escape(chapter_title)}</h2>
                {ordered_content_html}
            </body>
            </html>
            """

            chapter_item = epub.EpubHtml(
                title=chapter_title,
                file_name=chapter_file_name,
                lang=EPUB_LANGUAGE,
            )
            chapter_item.content = chapter_html
            chapter_item.add_item(style_item)

            epub_book.add_item(chapter_item)
            spine.append(chapter_item)
            chapter_items.append(chapter_item)

        toc.append((intro, chapter_items))

        epub_book.toc = toc
        epub_book.spine = spine

        epub_book.add_item(epub.EpubNcx())
        epub_book.add_item(epub.EpubNav())

        if epub_path.exists():
            epub_path.unlink()

        epub.write_epub(str(epub_path), epub_book)

        return epub_path

    # =====================================================
    # 第八步：总流程
    # =====================================================

    def crawl(
        self,
        keyword: str,
        max_volumes=None,
        max_chapters=None,
        target_volume=None,
        target_chapter=None,
    ):
        if target_chapter is not None and target_volume is None:
            raise RuntimeError("指定 --chapter 时必须同时指定 --volume。")

        SAVE_DIR.mkdir(parents=True, exist_ok=True)

        book_url = self.resolve_book_url(keyword)
        book_info = self.parse_book_info(book_url)

        book_title = safe_name(book_info["title"])
        book_dir = SAVE_DIR / book_title
        book_dir.mkdir(parents=True, exist_ok=True)

        epubs_dir = book_dir / "epubs"
        epubs_dir.mkdir(parents=True, exist_ok=True)

        image_dir = book_dir / "_epub_images_tmp"
        image_dir.mkdir(parents=True, exist_ok=True)

        print("\n========== 小说信息 ==========")
        print(f"小说名：{book_info['title']}")
        print(f"作者：{book_info.get('author')}")
        print(f"小说ID：{book_info['book_id']}")
        print(f"详情页：{book_info['url']}")
        print(f"保存目录：{book_dir}")
        print(f"EPUB 输出目录：{epubs_dir}")
        print("==============================\n")

        if SAVE_JSON:
            with open(book_dir / "book_info.json", "w", encoding="utf-8") as f:
                json.dump(book_info, f, ensure_ascii=False, indent=2)

        print("\n开始构建卷列表...")
        all_volumes = self.build_volume_list(book_info)

        if not all_volumes:
            raise RuntimeError("没有解析到任何卷或章节，无法生成 EPUB。")

        total_volumes = len(all_volumes)

        if target_volume is not None:
            if target_volume < 1 or target_volume > total_volumes:
                raise RuntimeError(
                    f"指定的卷号超出范围：第 {target_volume} 卷。"
                    f"当前共解析到 {total_volumes} 卷。"
                )

            selected_volumes = [(target_volume, all_volumes[target_volume - 1])]
            print(f"已指定只爬取第 {target_volume} 卷。")
        else:
            if max_volumes is not None:
                all_volumes = all_volumes[:max_volumes]

            selected_volumes = list(enumerate(all_volumes, 1))

        print(f"最终需要处理的卷数：{len(selected_volumes)}")

        generated_epubs = []

        for current_volume_order, (volume_index, volume_info) in enumerate(
            selected_volumes,
            1,
        ):
            print("\n" + "=" * 80)
            print(
                f"开始处理第 {volume_index} 卷"
                f"（本次第 {current_volume_order}/{len(selected_volumes)} 卷）："
                f"{volume_info['title']}"
            )
            print("=" * 80)

            volume_title = safe_name(volume_info["title"])
            volume_dir = book_dir / f"{volume_index:02d}_{volume_title}"

            if SAVE_TXT or SAVE_MD or SAVE_JSON:
                volume_dir.mkdir(parents=True, exist_ok=True)

            if SAVE_JSON:
                with open(volume_dir / "volume_info.json", "w", encoding="utf-8") as f:
                    json.dump(volume_info, f, ensure_ascii=False, indent=2)

            chapters = volume_info["chapters"]
            total_chapters = len(chapters)

            if target_chapter is not None:
                if target_chapter < 1 or target_chapter > total_chapters:
                    raise RuntimeError(
                        f"指定的章节号超出范围：第 {target_chapter} 章。"
                        f"第 {volume_index} 卷共解析到 {total_chapters} 章。"
                    )

                selected_chapters = [(target_chapter, chapters[target_chapter - 1])]
                print(f"已指定只爬取第 {volume_index} 卷第 {target_chapter} 章。")
            else:
                if max_chapters is not None:
                    chapters = chapters[:max_chapters]

                selected_chapters = list(enumerate(chapters, 1))

            print(f"本卷本次需要处理的章节数：{len(selected_chapters)}")

            chapter_results = []

            volume_txt_path = volume_dir / f"{volume_title}.txt"
            txt_file = None

            if SAVE_TXT:
                txt_file = open(volume_txt_path, "w", encoding="utf-8")
                txt_file.write(book_info["title"] + "\n")
                txt_file.write(f"作者：{book_info.get('author')}\n")
                txt_file.write(f"分卷：{volume_info['title']}\n")
                txt_file.write(f"来源：{book_info['url']}\n\n")

            try:
                for chapter_index, chapter in tqdm(
                    selected_chapters,
                    desc=volume_title,
                ):
                    chapter_title = safe_name(chapter["title"])
                    chapter_dir = volume_dir / f"{chapter_index:03d}_{chapter_title}"

                    if SAVE_MD or SAVE_JSON:
                        chapter_dir.mkdir(parents=True, exist_ok=True)

                    try:
                        chapter_data = self.parse_chapter(
                            chapter=chapter,
                            chapter_dir=chapter_dir,
                            image_dir=image_dir,
                            volume_order=volume_index,
                            chapter_order=chapter_index,
                        )
                    except Exception as e:
                        print(f"\n章节解析失败：{chapter['title']}")
                        print(f"URL：{chapter['url']}")
                        print(f"原因：{e}")
                        continue

                    chapter_results.append(chapter_data)

                    if SAVE_MD:
                        md_path = chapter_dir / f"{chapter_title}.md"

                        with open(md_path, "w", encoding="utf-8") as f:
                            f.write(f"# {chapter_data['title']}\n\n")
                            f.write(f"来源：{chapter_data['url']}\n\n")
                            f.write(blocks_to_markdown(chapter_data.get("blocks", [])))

                    if SAVE_JSON:
                        with open(chapter_dir / "chapter_info.json", "w", encoding="utf-8") as f:
                            json.dump(chapter_data, f, ensure_ascii=False, indent=2)

                    if txt_file:
                        txt_file.write(f"\n\n## {chapter_data['title']}\n\n")
                        txt_file.write(blocks_to_plain_text(chapter_data.get("blocks", [])))
                        txt_file.write("\n\n")

            finally:
                if txt_file:
                    txt_file.close()

            volume_result = {
                "title": volume_info["title"],
                "url": volume_info["url"],
                "chapters": chapter_results,
            }

            if not chapter_results:
                print(f"第 {volume_index} 卷没有成功解析任何章节，跳过生成 EPUB。")
                continue

            print(f"\n开始生成第 {volume_index} 卷 EPUB...")
            epub_path = self.create_epub_for_volume(
                book_info=book_info,
                volume_result=volume_result,
                volume_index=volume_index,
                epubs_dir=epubs_dir,
            )

            generated_epubs.append(str(epub_path))

            print(f"第 {volume_index} 卷 EPUB 已生成：{epub_path}")

        if SAVE_JSON:
            with open(book_dir / "generated_epubs.json", "w", encoding="utf-8") as f:
                json.dump(generated_epubs, f, ensure_ascii=False, indent=2)

        if image_dir.exists():
            shutil.rmtree(image_dir, ignore_errors=True)

        print("\n========== 全部完成 ==========")
        print(f"小说保存目录：{book_dir}")
        print(f"EPUB 输出目录：{epubs_dir}")
        print("生成的 EPUB：")

        for item in generated_epubs:
            print(item)

        print("==============================\n")


# =========================================================
# 程序入口
# =========================================================

def main():
    parser = argparse.ArgumentParser(
        description="linovelib.com 小说公开页面按卷生成 EPUB 工具"
    )

    parser.add_argument(
        "keyword",
        nargs="?",
        default=NOVEL_KEYWORD,
        help="小说名 / 小说ID / 小说详情页URL / 章节URL。不填则使用代码中的 NOVEL_KEYWORD",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="每次请求的间隔秒数",
    )

    parser.add_argument(
        "--scan-pages",
        type=int,
        default=DEFAULT_SCAN_PAGES,
        help="按小说名搜索时扫描文库页数",
    )

    parser.add_argument(
        "--max-volumes",
        type=int,
        default=DEFAULT_MAX_VOLUMES,
        help="最多爬取前 N 卷",
    )

    parser.add_argument(
        "--max-chapters",
        type=int,
        default=DEFAULT_MAX_CHAPTERS,
        help="每卷最多爬取前 N 章",
    )

    parser.add_argument(
        "--volume",
        type=int,
        default=None,
        help="只爬取第 N 卷，卷号从 1 开始",
    )

    parser.add_argument(
        "--chapter",
        type=int,
        default=None,
        help="只爬取指定卷中的第 N 章，章节号从 1 开始。使用时必须同时指定 --volume",
    )

    parser.add_argument(
        "--include-spoiler-images",
        action="store_true",
        help="包含网页隐藏的完整插图。该区域通常提示有剧透，默认不包含。",
    )

    args = parser.parse_args()

    global INCLUDE_SPOILER_IMAGES
    INCLUDE_SPOILER_IMAGES = args.include_spoiler_images

    if not args.keyword or str(args.keyword).strip() == "":
        raise RuntimeError(
            "请先在代码上方的 NOVEL_KEYWORD 里填写小说名、小说ID或小说详情页链接。"
        )

    if args.volume is not None and args.volume < 1:
        raise RuntimeError("--volume 必须是大于等于 1 的整数。")

    if args.chapter is not None and args.chapter < 1:
        raise RuntimeError("--chapter 必须是大于等于 1 的整数。")

    if args.chapter is not None and args.volume is None:
        raise RuntimeError("指定 --chapter 时必须同时指定 --volume。")

    print("\n========== 当前配置 ==========")
    print(f"小说关键词 / ID / 链接：{args.keyword}")
    print(f"请求间隔：{args.delay} 秒")
    print(f"扫描文库页数：{args.scan_pages}")
    print(f"最大爬取卷数：{args.max_volumes}")
    print(f"每卷最大章节数：{args.max_chapters}")
    print(f"指定卷号：{args.volume}")
    print(f"指定章节号：{args.chapter}")
    print(f"是否下载并嵌入图片：{DOWNLOAD_IMAGES}")
    print(f"是否包含剧透完整插图：{INCLUDE_SPOILER_IMAGES}")
    print("EPUB 生成方式：每一卷单独生成一个 EPUB")
    print("================================\n")

    crawler = LinovelibVolumeEpubCrawler(
        delay=args.delay,
        scan_pages=args.scan_pages,
    )

    crawler.crawl(
        keyword=args.keyword,
        max_volumes=args.max_volumes,
        max_chapters=args.max_chapters,
        target_volume=args.volume,
        target_chapter=args.chapter,
    )


if __name__ == "__main__":
    main()
