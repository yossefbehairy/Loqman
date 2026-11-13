import asyncio

import pytest

from backend.pyworker.core.browser import BrowserSession, BrowserError


@pytest.mark.asyncio
async def test_real_browser_reads_fills_clicks_and_yields_control():
    page = b"""<html><body><input aria-label="name"><button onclick="
      document.querySelector('p').innerText='Hello '+document.querySelector('input').value">Show</button>
      <p>Waiting</p></body></html>"""

    async def serve(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                     + str(len(page)).encode() + b"\r\nConnection: close\r\n\r\n" + page)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    browser = BrowserSession()
    try:
        try:
            snapshot = await browser.call("browser_open", {"url": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"})
        except BrowserError as error:
            if str(error).startswith("Install Chromium"):
                pytest.skip(str(error))
            raise
        input_ref = next(e["ref"] for e in snapshot["elements"] if e["label"] == "name")
        await browser.call("browser_fill", {"ref": input_ref, "text": "AgentOS"})
        snapshot = await browser.snapshot()
        button_ref = next(e["ref"] for e in snapshot["elements"] if e["label"] == "Show")
        browser.human_control = True
        with pytest.raises(BrowserError, match="belongs to the user"):
            await browser.call("browser_click", {"ref": button_ref})
        assert "Hello AgentOS" not in (await browser.snapshot())["text"]
        browser.human_control = False
        await browser.call("browser_click", {"ref": button_ref})
        result = await browser.call("browser_wait", {"text": "Hello AgentOS"})
        assert result["untrusted"] and "Hello AgentOS" in result["text"]
        import base64
        screenshot = await browser.call("browser_screenshot", {})
        assert base64.b64decode(screenshot["image"]).startswith(b"\xff\xd8")
        with pytest.raises(BrowserError):
            await browser.call("browser_open", {"url": "file:///C:/Windows/win.ini"})
    finally:
        server.close()
        await server.wait_closed()
        await browser.close()
    assert browser.process.returncode is not None
