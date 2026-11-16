"""Isolated Chromium session controlled through a bounded stdlib CDP client."""

import asyncio
import base64
import hashlib
import json
import ipaddress
import os
import socket
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen


class BrowserError(Exception):
    pass


def _check_public_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError()
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0].split("%")[0]).is_global for item in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise BrowserError("Browser URL must resolve only to public addresses.") from None


class Cdp:
    async def connect(self, url):
        parsed = urlsplit(url)
        self.reader, self.writer = await asyncio.open_connection(parsed.hostname, parsed.port)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.writer.write(
            (f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.netloc}\r\nUpgrade: websocket\r\n"
             f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        await self.writer.drain()
        headers = await asyncio.wait_for(self.reader.readuntil(b"\r\n\r\n"), 5)
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if b" 101 " not in headers.split(b"\r\n")[0] or expected.lower() not in headers.lower():
            raise BrowserError("Browser websocket handshake failed.")
        self.next_id = 0

    async def send(self, data, opcode=1):
        mask = secrets.token_bytes(4)
        size = len(data)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", size)
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
        self.writer.write(header + mask + payload)
        await self.writer.drain()

    async def receive(self):
        chunks = bytearray()
        while True:
            first, second = await self.reader.readexactly(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if size + len(chunks) > 2 * 1024 * 1024:
                raise BrowserError("Browser response exceeded limit.")
            if second & 128:
                raise BrowserError("Invalid browser websocket frame.")
            payload = await self.reader.readexactly(size)
            opcode = first & 15
            if opcode == 8:
                raise BrowserError("Browser connection closed.")
            if opcode == 9:
                await self.send(payload, 10)
                continue
            if opcode == 10:
                continue
            chunks.extend(payload)
            if first & 128:
                return json.loads(chunks)

    async def call(self, method, params=None):
        self.next_id += 1
        request_id = self.next_id
        await self.send(json.dumps({"id": request_id, "method": method, "params": params or {}}).encode())
        async with asyncio.timeout(20):
            while True:
                message = await self.receive()
                if message.get("method") == "Fetch.requestPaused":
                    paused = message["params"]
                    try:
                        await asyncio.to_thread(_check_public_url, paused["request"]["url"])
                        method = "Fetch.continueRequest"
                    except BrowserError:
                        method = "Fetch.failRequest"
                    self.next_id += 1
                    params = {"requestId": paused["requestId"]}
                    if method == "Fetch.failRequest":
                        params["errorReason"] = "BlockedByClient"
                    await self.send(json.dumps({"id": self.next_id, "method": method, "params": params}).encode())
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise BrowserError("Browser command failed.")
                return message.get("result", {})

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


class BrowserSession:
    def __init__(self):
        self.process = None
        self.profile = None
        self.cdp = None
        self.human_control = False

    async def start(self):
        candidates = [
            shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Google/Chrome/Application/chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft/Edge/Application/msedge.exe"),
        ]
        executable = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not executable:
            raise BrowserError("Install Chromium, Chrome or Edge to enable browser tools.")
        self.profile = tempfile.TemporaryDirectory(prefix="agentos-browser-")
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}
        self.process = await asyncio.create_subprocess_exec(
            executable, "--headless=new", "--no-first-run", "--no-default-browser-check",
            # CDP currently uses WebSockets, so local processes can still connect.
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", f"--user-data-dir={self.profile.name}", "about:blank",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, **kwargs)
        active_port = Path(self.profile.name) / "DevToolsActivePort"
        for _ in range(100):
            if active_port.exists():
                break
            if self.process.returncode is not None:
                raise BrowserError("Chromium failed to start.")
            await asyncio.sleep(.1)
        if not active_port.exists():
            raise BrowserError("Chromium debugging endpoint did not become ready.")
        port = int(active_port.read_text().splitlines()[0])

        def targets():
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                return json.loads(response.read(1024 * 1024))

        pages = await asyncio.to_thread(targets)
        page = next((p for p in pages if p.get("type") == "page"), None)
        if page is None:
            raise BrowserError("Browser page unavailable.")
        self.cdp = Cdp()
        await self.cdp.connect(page["webSocketDebuggerUrl"])
        await self.cdp.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})

    async def evaluate(self, expression):
        result = await self.cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if result.get("exceptionDetails"):
            raise BrowserError("Page changed or the requested element is unavailable.")
        return result.get("result", {}).get("value")

    async def snapshot(self, text_offset=0, element_offset=0):
        if type(text_offset) is not int or text_offset < 0 or type(element_offset) is not int or element_offset < 0:
            raise BrowserError("Snapshot offsets must be nonnegative integers.")
        expression = """(() => {
          const nodes = [...document.querySelectorAll('a,button,input,textarea,select,[role=button]')]
            .filter(e => e.getClientRects().length).slice(0,5000);
          window.__agentosElements = nodes;
          const text = document.body?.innerText || '';
          const textOffset = TEXT_OFFSET, elementOffset = ELEMENT_OFFSET;
          return {url:location.href,title:document.title,
            text:text.slice(textOffset,textOffset+16000),untrusted:true,
            next_text_offset:textOffset+16000<text.length?textOffset+16000:null,
            next_element_offset:elementOffset+150<nodes.length?elementOffset+150:null,
            elements:nodes.slice(elementOffset,elementOffset+150).map((e,i)=>({ref:i+1+elementOffset,tag:e.tagName.toLowerCase(),
              label:(e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||'').slice(0,150),
              type:e.getAttribute('type')}))};
        })()"""
        return await self.evaluate(expression.replace("TEXT_OFFSET", str(text_offset)).replace("ELEMENT_OFFSET", str(element_offset)))

    async def call(self, name, args):
        if self.human_control:
            raise BrowserError("Browser control belongs to the user.")
        if self.process is None:
            await self.start()
        if self.human_control:
            raise BrowserError("Browser control belongs to the user.")
        if name == "browser_open":
            url = args.get("url")
            if not isinstance(url, str) or len(url) > 4096 or urlsplit(url).scheme not in ("http", "https"):
                raise BrowserError("Browser URL must use http or https.")
            await asyncio.to_thread(_check_public_url, url)
            navigation = await self.cdp.call("Page.navigate", {"url": url})
            if navigation.get("errorText"):
                raise BrowserError("Browser navigation failed.")
            await self.wait_ready()
        elif name in ("browser_click", "browser_fill"):
            ref = args.get("ref")
            if type(ref) is not int or not 1 <= ref <= 5000:
                raise BrowserError("Use a ref from the latest browser snapshot.")
            element = f"window.__agentosElements?.[{ref-1}]"
            if name == "browser_click":
                await self.evaluate(f"(() => {{const e={element}; if(!e?.isConnected)throw Error(); e.click(); return true;}})()")
                await self.wait_ready()
            else:
                text = args.get("text")
                if not isinstance(text, str) or len(text) > 10000:
                    raise BrowserError("Input must contain at most 10000 characters.")
                await self.evaluate(f"""(() => {{const e={element}; if(!e?.isConnected)throw Error();
                  if(!['INPUT','TEXTAREA'].includes(e.tagName))throw Error();
                  const proto=e.tagName==='INPUT'?HTMLInputElement.prototype:HTMLTextAreaElement.prototype;
                  Object.getOwnPropertyDescriptor(proto,'value').set.call(e,{json.dumps(text)});
                  e.dispatchEvent(new Event('input',{{bubbles:true}}));
                  e.dispatchEvent(new Event('change',{{bubbles:true}})); return true;}})()""")
        elif name == "browser_wait":
            text = args.get("text")
            if not isinstance(text, str) or not text or len(text) > 1000:
                raise BrowserError("Provide text to wait for.")
            async with asyncio.timeout(15):
                while not await self.evaluate(f"document.body?.innerText.includes({json.dumps(text)})"):
                    await asyncio.sleep(.2)
        elif name == "browser_screenshot":
            image = await self.cdp.call("Page.captureScreenshot", {
                "format": "jpeg", "quality": 60, "captureBeyondViewport": False})
            return {"image": image["data"], "mimeType": "image/jpeg",
                    "url": await self.evaluate("location.href"), "untrusted": True}
        elif name != "browser_snapshot":
            raise BrowserError("Unknown browser tool.")
        return await self.snapshot(args.get("text_offset", 0), args.get("element_offset", 0))

    async def wait_ready(self):
        await asyncio.sleep(.1)
        async with asyncio.timeout(15):
            while True:
                try:
                    if await self.evaluate("document.readyState") == "complete":
                        return
                except BrowserError:
                    pass
                await asyncio.sleep(.1)

    async def close(self):
        if self.cdp is not None:
            await self.cdp.close()
        if self.process is not None and self.process.returncode is None:
            if sys.platform == "win32":
                await asyncio.to_thread(subprocess.run,
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                self.process.kill()
            await self.process.wait()
        if self.profile is not None:
            self.profile.cleanup()
