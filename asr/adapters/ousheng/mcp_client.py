"""MCP client for OuSheng — the action channel (writeback, P3).

Spawns `ousheng-mcp` (overridable) and speaks newline-delimited JSON-RPC
(MCP stdio). This is the ONLY sanctioned write path to a board: every
call lands in OuSheng's own hard gates (identity, CAS, state machine).

Safety properties:
- never edits YAML by hand;
- synchronous request/response matching by id; non-JSON banner lines on
  stdout are skipped;
- reader thread with per-call timeout — a hung server raises instead of
  deadlocking the pipeline.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from queue import Empty, Queue


class MCPError(Exception):
    pass


class OuShengMCPClient:
    def __init__(self, board_dir: str, cmd: list[str] | None = None,
                 timeout: float = 60.0):
        self.board_dir = str(board_dir)
        self._cmd = cmd or os.environ.get("MODOU_MCP_CMD", "ousheng-mcp").split()
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._queue: Queue = Queue()
        self._id = 0
        self._reader: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def __enter__(self) -> "OuShengMCPClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [*self._cmd],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        resp = self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "modou", "version": "0.1"},
        })
        if "error" in resp:
            raise MCPError(f"initialize failed: {resp['error']}")
        self._notify("notifications/initialized")

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=10)
        except Exception:
            self._proc.kill()
        finally:
            self._proc = None

    # ── io ───────────────────────────────────────────────────────────────
    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue  # banner/noise lines
            try:
                self._queue.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _send(self, payload: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                msg = self._queue.get(timeout=self._timeout)
            except Empty as e:
                raise MCPError(f"timeout waiting for {method} response") from e
            if msg.get("id") == rid:
                return msg
            # notifications or unrelated ids: keep waiting

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    # ── API ──────────────────────────────────────────────────────────────
    def list_tools(self) -> list[dict]:
        resp = self._request("tools/list", {})
        if "error" in resp:
            raise MCPError(str(resp["error"]))
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        resp = self._request("tools/call", {"name": name, "arguments": arguments})
        if "error" in resp:
            raise MCPError(f"{name} error: {resp['error'].get('message') or resp['error']}")
        result = resp.get("result", {})
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", [])
                     if c.get("type") == "text"]
            raise MCPError(f"{name} tool-error: {' '.join(texts)[:300]}")
        return result
