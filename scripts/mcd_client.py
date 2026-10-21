"""零依赖麦当劳 MCP 客户端（Streamable HTTP）。

本模块只做四件事：initialize / tools/list / tools/call / 错误归类。
不引入任何第三方库，只用标准库 urllib，方便在任何 WorkBuddy 沙箱里直接运行。

协议要点（来自官方 mcd-mcp-server 文档）：
- 接入地址：https://mcp.mcd.cn
- 传输：Streamable HTTP
- 鉴权：Authorization: Bearer <MCD_MCP_TOKEN>
- 限流：单 Token 600 次/分钟，超限返回 429；Token 无效返回 401
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://mcp.mcd.cn"


class McpError(RuntimeError):
    """MCP 调用失败的统一异常，带上可读的原因归类。"""

    def __init__(self, message: str, code: str = "MCP_ERROR", hint: str = ""):
        super().__init__(message)
        self.code = code
        self.hint = hint


class McpClient:
    """极简 MCP 客户端。

    只支持 Streamable HTTP。一次构造对应一个会话，内部维护 Mcp-Session-Id。
    """

    def __init__(self, token: str | None = None, base_url: str = DEFAULT_BASE_URL, timeout: int = 30):
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 0
        self._initialized = False

    # ------------------------------------------------------------------ 基础

    def _post(self, payload: dict) -> dict | None:
        """发一条 JSON-RPC，返回解析后的 result（通知类请求返回 None）。"""
        if not self.token:
            raise McpError(
                "未提供 MCD_MCP_TOKEN",
                code="NO_TOKEN",
                hint="前往 https://open.mcd.cn/mcp 手机号登录，在控制台激活并复制 Token，"
                     "然后 export MCD_MCP_TOKEN=... 或传入 --token",
            )

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "mcd-wish-order/0.1",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(self.base_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", "ignore")
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            if e.code == 401:
                raise McpError(
                    "Token 无效或已过期（401）",
                    code="UNAUTHORIZED",
                    hint="回到 https://open.mcd.cn/mcp 控制台重新生成 Token",
                ) from e
            if e.code == 429:
                raise McpError(
                    "触发限流（429）：单 Token 上限 600 次/分钟",
                    code="RATE_LIMITED",
                    hint="降低调用频率，批量场景请加节流",
                ) from e
            raise McpError(f"HTTP {e.code} {detail}", code=f"HTTP_{e.code}") from e
        except urllib.error.URLError as e:
            raise McpError(f"网络不可达：{e.reason}", code="NETWORK") from e

        if not raw.strip():
            return None
        return self._parse(raw, ctype, payload.get("id"))

    @staticmethod
    def _parse(raw: str, ctype: str, want_id) -> dict | None:
        """兼容两种响应形态：纯 JSON，以及 SSE（data: {...}）。"""
        if "text/event-stream" in ctype or raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
            picked = None
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if want_id is None or obj.get("id") == want_id:
                    picked = obj
            msg = picked
        else:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                raise McpError(f"无法解析响应：{raw[:200]}", code="BAD_RESPONSE")

        if msg is None:
            return None
        if isinstance(msg, list):
            msg = next((m for m in msg if isinstance(m, dict) and m.get("id") == want_id), msg[0])
        if "error" in msg and msg["error"]:
            err = msg["error"]
            raise McpError(
                f"MCP 返回错误：{err.get('message', err)}",
                code="TOOL_ERROR",
                hint=str(err.get("data", ""))[:200],
            )
        return msg.get("result")

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        if notify:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})
            return None
        self._next_id += 1
        return self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        )

    # ------------------------------------------------------------------ 协议

    def initialize(self) -> dict:
        if self._initialized:
            return {}
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "mcd-wish-order", "version": "0.1"},
            },
        )
        self._rpc("notifications/initialized", notify=True)
        self._initialized = True
        return result or {}

    def list_tools(self) -> list[dict]:
        self.initialize()
        result = self._rpc("tools/list") or {}
        return result.get("tools", [])

    def call(self, name: str, arguments: dict | None = None) -> dict:
        """调用一个工具，返回解析后的结构化内容。

        MCP 的 tools/call 返回 {content: [{type:"text", text:"..."}], isError?}，
        业务数据通常藏在 text 里的 JSON 或 Markdown 中，这里做尽力解析。
        """
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}
        if result.get("isError"):
            raise McpError(f"工具 {name} 返回 isError", code="TOOL_ERROR", hint=str(result)[:300])
        return self._unwrap(result)

    @staticmethod
    def _unwrap(result: dict):
        """把 content 数组拆成「结构化数据 + 原始文本」两部分。"""
        texts = []
        for item in result.get("content", []) or []:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        joined = "\n".join(t for t in texts if t)

        # 尝试整体当 JSON
        for cand in (joined, joined.strip().strip("`")):
            if cand.startswith("{") or cand.startswith("["):
                try:
                    return json.loads(cand)
                except json.JSONDecodeError:
                    pass

        # 尝试从 Markdown 代码块里抠 JSON（部分工具会把数据包在 ```json 里）
        for block in McpClient._code_blocks(joined):
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue

        # 兜底：返回原文，让上层自己处理
        return {"_text": joined, "_structured": False}

    @staticmethod
    def _code_blocks(text: str):
        lines = text.splitlines()
        buf, inside = [], False
        for line in lines:
            if line.strip().startswith("```"):
                if inside:
                    yield "\n".join(buf).strip()
                    buf, inside = [], False
                else:
                    inside = True
                continue
            if inside:
                buf.append(line)
        if buf:
            yield "\n".join(buf).strip()

    # ------------------------------------------------------------- 便捷封装

    def probe(self) -> dict:
        """连通性自检：握手 + 工具清单。用来证明数据链路是真的。"""
        info = self.initialize()
        tools = self.list_tools()
        return {
            "ok": True,
            "server": info.get("serverInfo", {}),
            "protocolVersion": info.get("protocolVersion"),
            "tool_count": len(tools),
            "tools": [t.get("name") for t in tools],
        }


if __name__ == "__main__":
    import sys

    try:
        report = McpClient().probe()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except McpError as exc:
        print(f"[{exc.code}] {exc}")
        if exc.hint:
            print(f"提示：{exc.hint}")
        sys.exit(1)
