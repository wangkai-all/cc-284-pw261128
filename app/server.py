"""HTTP API + 静态页面服务（仅标准库）。

启动：python3 -I -m app.server --data data/state.json --port 8000
所有写操作在 Ledger 内加锁并原子落盘；风险分析始终服务端实时重算。
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .ledger import Ledger, LedgerError
from .treatment import PlanError
from .qi import QIError

STATIC_DIR = "static"

_LEDGER: Ledger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger(path: str | None = None) -> Ledger:
    global _LEDGER
    if _LEDGER is None:
        with _LEDGER_LOCK:
            if _LEDGER is None:
                _LEDGER = Ledger(path or "data/state.json")
    elif path is not None and _LEDGER.path != os.path.abspath(path):
        with _LEDGER_LOCK:
            _LEDGER = Ledger(path)
    return _LEDGER


def reset_ledger_for_tests() -> None:
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = None


class Handler(BaseHTTPRequestHandler):
    server_version = "DeidReview/1.0"

    # ---------- 基础收发 ----------
    def _send_json(self, obj, status: int = 200, headers: dict | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LedgerError(f"请求体不是合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LedgerError("请求体必须是 JSON 对象")
        return data

    def _error(self, exc: Exception, status: int = 400) -> None:
        self._send_json({"error": type(exc).__name__, "message": str(exc)}, status)

    def log_message(self, fmt, *args):  # 静默默认访问日志，保留错误日志
        return

    # ---------- 路由 ----------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                return self._send_json(self._ledger().snapshot())
            if path == "/api/stats":
                return self._send_json(self._ledger().stats())
            if path == "/api/policy":
                return self._send_json(self._ledger().policy())
            if path == "/api/candidates":
                return self._send_json(self._ledger().list_candidates())
            if path.startswith("/api/candidates/"):
                cid = path.rsplit("/", 1)[-1]
                return self._send_json(self._ledger().get_candidate(cid))
            if path.startswith("/api/analysis/"):
                cid = path.rsplit("/", 1)[-1]
                return self._send_json(self._ledger().analyze_candidate(cid))
            if path == "/api/releases":
                return self._send_json(self._ledger().list_releases())
            if path.startswith("/api/download/"):
                rid = path.rsplit("/", 1)[-1]
                pkg = self._ledger().download_artifact(rid)
                return self._send_json(
                    pkg["content"],
                    headers={
                        "Content-Disposition":
                            f"attachment; filename=\"{pkg['filename']}\"; "
                            f"filename*={pkg['filename_quoted']}"
                    },
                )
            if path == "/api/audit":
                return self._send_json(self._ledger().audit_log())
            return self._serve_static(path)
        except (LedgerError, PlanError, QIError) as exc:
            return self._error(exc, 400)
        except KeyError as exc:
            return self._error(LedgerError(f"资源不存在: {exc}"), 404)
        except Exception as exc:  # 兜底：返回 500 而不是直接断开连接
            self.log_error("unhandled: %s", exc)
            return self._error(exc, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            ledger = self._ledger()
            if path.startswith("/api/analysis/"):
                cid = path.rsplit("/", 1)[-1]
                return self._send_json(
                    ledger.analyze_candidate(cid, data.get("plan"))
                )
            if path.startswith("/api/publish/"):
                cid = path.rsplit("/", 1)[-1]
                if "plan" not in data:
                    raise LedgerError("发布请求必须携带 plan")
                return self._send_json(ledger.publish(cid, data["plan"]), 201)
            if path == "/api/candidates":
                return self._send_json(ledger.add_candidate(data), 201)
            return self._error(LedgerError(f"未知接口: {path}"), 404)
        except (LedgerError, PlanError, QIError) as exc:
            return self._error(exc, 400)
        except Exception as exc:
            self.log_error("unhandled: %s", exc)
            return self._error(exc, 500)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            ledger = self._ledger()
            if path == "/api/policy":
                return self._send_json(
                    ledger.update_policy(data["small_threshold"]))
            if path.startswith("/api/withdraw/"):
                rid = path.rsplit("/", 1)[-1]
                return self._send_json(
                    ledger.withdraw(rid, data.get("reason", "")))
            return self._error(LedgerError(f"未知接口: {path}"), 404)
        except (LedgerError, PlanError, QIError) as exc:
            return self._error(exc, 400)
        except Exception as exc:
            self.log_error("unhandled: %s", exc)
            return self._error(exc, 500)

    # ---------- 静态资源 ----------
    def _serve_static(self, path: str) -> None:
        import os
        if path == "/":
            path = "/index.html"
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), STATIC_DIR)
        target = os.path.normpath(os.path.join(base, path.lstrip("/")))
        if not target.startswith(base + os.sep) or not os.path.isfile(target):
            self._send_json({"error": "NotFound", "message": path}, 404)
            return
        ext = os.path.splitext(target)[1]
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(ext, "application/octet-stream")
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _ledger() -> Ledger:
        return get_ledger()


def main() -> None:
    parser = argparse.ArgumentParser(description="去标识数据发布审查台")
    parser.add_argument("--data", default="data/state.json", help="状态文件路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    get_ledger(args.data)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"发布审查台已启动: http://{args.host}:{args.port}/  (数据: {args.data})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
