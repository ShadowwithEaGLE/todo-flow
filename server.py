#!/usr/bin/env python3
"""Small trusted-LAN server for Flow Board; uses only Python's standard library."""

import argparse
import json
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "flow-board.sqlite3"
PAGE_PATH = ROOT / "todo-flow.html"
MAX_BODY = 2 * 1024 * 1024
SESSION_SECONDS = 8 * 60 * 60
DEFAULT_STATE = {"tasks": [], "fixed": [], "notes": [], "log": []}
ARRAY_LIMITS = {"tasks": 5000, "fixed": 1000, "notes": 20000, "log": 2000}
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
ONLINE_CLIENTS = {}
ONLINE_LOCK = threading.Lock()


def get_online_users():
    with ONLINE_LOCK:
        return list(ONLINE_CLIENTS.values())


class EventHub:
    def __init__(self):
        self.condition = threading.Condition()
        self.revision = 0

    def publish(self, revision):
        with self.condition:
            self.revision = revision
            self.condition.notify_all()

    def wait_after(self, revision, timeout):
        with self.condition:
            if self.revision <= revision:
                self.condition.wait(timeout)
            return self.revision


EVENTS = EventHub()


def db_connect():
    return sqlite3.connect(DB_PATH, timeout=10)


def init_database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(db_connect()) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS board_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "payload TEXT NOT NULL, revision INTEGER NOT NULL, "
            "initialized INTEGER NOT NULL)"
        )
        db.execute(
            "INSERT OR IGNORE INTO board_state "
            "(id, payload, revision, initialized) VALUES (1, ?, 0, 0)",
            (json.dumps(DEFAULT_STATE, ensure_ascii=False),),
        )
        db.commit()
        row = db.execute(
            "SELECT revision FROM board_state WHERE id = 1"
        ).fetchone()
    EVENTS.revision = row[0]


def current_record():
    with closing(db_connect()) as db:
        row = db.execute(
            "SELECT payload, revision, initialized FROM board_state WHERE id = 1"
        ).fetchone()
    return {
        "state": json.loads(row[0]),
        "revision": row[1],
        "initialized": bool(row[2]),
    }


def normalize_state(value):
    if not isinstance(value, dict):
        raise ValueError("数据必须是 JSON 对象")
    result = {}
    for key, limit in ARRAY_LIMITS.items():
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError(key + " 数据格式错误或数量超限")
        clean = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(key + " 中有无效记录")
            record_id = item.get("id")
            if key != "log" and (not isinstance(record_id, str) or not record_id or len(record_id) > 128):
                raise ValueError(key + " 记录缺少有效 id")
            if key == "notes" and (not isinstance(item.get("date"), str) or len(item["date"]) > 20):
                raise ValueError("便签日期无效")
            clean.append(item)
        result[key] = clean
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FlowBoard/1"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Request logs omit request bodies and the shared access code.
        super().log_message(fmt, *args)

    def send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("请求长度无效")
        if size <= 0 or size > MAX_BODY:
            raise ValueError("请求为空或超过 2 MB")
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ValueError("请求必须使用 JSON")
        try:
            return json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("JSON 格式无效")

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["flow_session"].value
        except (KeyError, ValueError):
            return None
        with SESSIONS_LOCK:
            record = SESSIONS.get(token)
            if not record:
                return None
            if record["expires"] < time.time():
                SESSIONS.pop(token, None)
                return None
            return record

    def require_session(self):
        record = self.session()
        if not record:
            self.send_json(401, {"error": "共享会话已失效，请重新加入。"})
            return None
        return record

    def check_origin(self):
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            self.send_json(403, {"error": "跨来源请求已拒绝。"})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/todo-flow.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/todo-flow.html":
            try:
                page = PAGE_PATH.read_bytes()
            except OSError:
                self.send_error(500, "todo-flow.html is missing")
                return
            marker = b"var FLOW_BOARD_SERVER = false;"
            if marker not in page:
                self.send_error(500, "LAN page marker is missing")
                return
            page = page.replace(marker, b"var FLOW_BOARD_SERVER = true;", 1)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if path == "/api/session":
            record = self.session()
            if record:
                self.send_json(200, {"name": record["name"]})
            else:
                self.send_json(401, {"error": "请先加入局域网共享。"})
            return
        if path == "/api/state":
            if not self.require_session():
                return
            self.send_json(200, current_record())
            return
        if path == "/api/events":
            if not self.require_session():
                return
            self.stream_events()
            return
        self.send_error(404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/api/login", "/api/logout", "/api/import", "/api/upgrade"):
            self.send_error(404)
            return
        if not self.check_origin():
            return
        try:
            data = self.read_json()
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        if path == "/api/login":
            name = data.get("name") if isinstance(data, dict) else None
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
                self.send_json(400, {"error": "显示名称需为 1 到 40 个字符。"})
                return
            token = secrets.token_urlsafe(32)
            clean_name = name.strip()
            with SESSIONS_LOCK:
                SESSIONS[token] = {"name": clean_name, "expires": time.time() + SESSION_SECONDS}
            cookie = (
                "flow_session=" + token +
                "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + str(SESSION_SECONDS)
            )
            self.send_json(200, {"name": clean_name}, [("Set-Cookie", cookie)])
            return
        if path == "/api/logout":
            record = self.session()
            if record:
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                token = cookie.get("flow_session")
                if token:
                    with SESSIONS_LOCK:
                        SESSIONS.pop(token.value, None)
            self.send_json(200, {"ok": True}, [("Set-Cookie", "flow_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")])
            return
        if path == "/api/upgrade":
            if not self.require_session():
                return
            url = data.get("url") if isinstance(data, dict) else ""
            if not isinstance(url, str) or not url.startswith("https://"):
                self.send_json(400, {"error": "升级链接无效"})
                return
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "FlowBoard-Server"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        raise ValueError(f"下载失败: HTTP {resp.status}")
                    content = resp.read()
                marker = b"var FLOW_BOARD_SERVER = false;"
                if marker not in content and b"FLOW_BOARD_SERVER" not in content:
                    raise ValueError("下载内容并非有效的 Flow Board 页面")
                # Write to temp file then atomic replace
                temp_file = PAGE_PATH.with_suffix(".tmp")
                temp_file.write_bytes(content)
                temp_file.replace(PAGE_PATH)
            except Exception as exc:
                self.send_json(500, {"error": f"更新失败: {exc}"})
                return
            self.send_json(200, {"ok": True, "message": "升级成功"})
            return
        if not self.require_session():
            return
        try:
            state = normalize_state(data)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        db = db_connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision, initialized FROM board_state WHERE id = 1"
            ).fetchone()
            if row[0] != 0 or row[1]:
                db.rollback()
                self.send_json(409, {"error": "共享库已初始化，不能再次导入。", **current_record()})
                return
            revision = row[0] + 1
            db.execute(
                "UPDATE board_state SET payload = ?, revision = ?, initialized = 1 WHERE id = 1",
                (json.dumps(state, ensure_ascii=False, separators=(",", ":")), revision),
            )
            db.commit()
        finally:
            db.close()
        EVENTS.publish(revision)
        self.send_json(200, {"state": state, "revision": revision, "initialized": True})

    def do_PUT(self):
        if urlsplit(self.path).path != "/api/state":
            self.send_error(404)
            return
        if not self.check_origin() or not self.require_session():
            return
        try:
            data = self.read_json()
            expected = data.get("expectedRevision")
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                raise ValueError("共享版本号无效")
            state = normalize_state(data.get("state"))
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        db = db_connect()
        conflict = None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload, revision, initialized FROM board_state WHERE id = 1"
            ).fetchone()
            if row[1] != expected:
                db.rollback()
                conflict = {
                    "state": json.loads(row[0]),
                    "revision": row[1],
                    "initialized": bool(row[2]),
                }
            else:
                revision = row[1] + 1
                db.execute(
                    "UPDATE board_state SET payload = ?, revision = ?, initialized = 1 WHERE id = 1",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), revision),
                )
                db.commit()
        finally:
            db.close()
        if conflict:
            self.send_json(409, {"error": "其他浏览器已先保存更新。", **conflict})
            return
        EVENTS.publish(revision)
        self.send_json(200, {"state": state, "revision": revision, "initialized": True})

    def stream_events(self):
        query = parse_qs(urlsplit(self.path).query)
        try:
            last = int(self.headers.get("Last-Event-ID") or query.get("since", ["0"])[0])
        except ValueError:
            last = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        session = self.session()
        client_ip = self.client_address[0]
        if client_ip in ("::1", "127.0.0.1"):
            client_ip = "本机"
        client_name = session["name"] if session else "匿名协作者"
        conn_id = secrets.token_hex(8)
        with ONLINE_LOCK:
            ONLINE_CLIENTS[conn_id] = {"name": client_name, "ip": client_ip}
        EVENTS.publish(current_record()["revision"])
        try:
            while True:
                revision = current_record()["revision"]
                if revision > last:
                    message = json.dumps({
                        "revision": revision,
                        "onlineUsers": get_online_users(),
                    }, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(("id: " + str(revision) + "\ndata: " + message + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    last = revision
                else:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                EVENTS.wait_after(last, 15)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return
        finally:
            with ONLINE_LOCK:
                ONLINE_CLIENTS.pop(conn_id, None)
            EVENTS.publish(current_record()["revision"])


def main():
    init_database()
    parser = argparse.ArgumentParser(description="Flow Board trusted-LAN server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口范围必须为 1 到 65535")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print("Flow Board 已启动：http://<本机局域网 IP>:" + str(args.port) + "/todo-flow.html")
    print("数据库：" + str(DB_PATH))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
