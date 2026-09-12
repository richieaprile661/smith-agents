"""Transient, local permission requests shared by the VS Code bridge and widget."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import sys
import tempfile
import threading

from .context_bridge import atomic_json

MAX_MESSAGE = 2 * 1024 * 1024
APPROVABLE_TOOLS = {"Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch"}


def endpoint_path(directory, session):
    return Path(directory) / ("permission-" + hashlib.sha256(session.encode()).hexdigest() + ".json")


def fingerprint(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PermissionRelay:
    def __init__(self, directory, send_child, send_client):
        self.directory = directory
        self.send_child = send_child
        self.send_client = send_client
        self.session = None
        self.pending = {}
        self.finished = {}  # True means answered here: suppress a late VS Code reply.
        self.lock = threading.RLock()
        self.token = secrets.token_hex(24)
        self.temp = None
        self.server = None
        self.paths = []
        self.address = {}

    def _finish(self, request_id, local=False):
        self.pending.pop(request_id, None)
        self.finished[request_id] = local
        if len(self.finished) > 512:
            del self.finished[next(iter(self.finished))]

    def _start(self):
        if self.server:
            return
        # Short path for macOS's Unix-socket length limit; the directory is 0700.
        self.temp = tempfile.TemporaryDirectory(prefix="smith-agents-", dir="/private/tmp" if os.path.isdir("/private/tmp") else None)
        relay = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(1)
                try:
                    raw = self.rfile.readline(MAX_MESSAGE + 1)
                    if len(raw) > MAX_MESSAGE:
                        return
                    message = json.loads(raw)
                    answer = relay.handle(message)
                    self.wfile.write(json.dumps(answer).encode() + b"\n")
                except (OSError, ValueError, TypeError, AttributeError):
                    return

        if sys.platform == "win32":
            self.server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
            self.address = {"port": self.server.server_address[1]}
        else:
            address = str(Path(self.temp.name) / "socket")
            self.server = socketserver.UnixStreamServer(address, Handler)
            self.address = {"socket": address}
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True).start()

    def observe(self, data):
        if not isinstance(data, dict):
            return
        with self.lock:
            session = data.get("session_id")
            if isinstance(session, str) and session and not data.get("parent_tool_use_id"):
                if self.session != session:
                    self.pending.clear()
                    self.session = session
                    self._start()
                    path = endpoint_path(self.directory, session)
                    atomic_json(path, {"session": session, **self.address,
                                       "token": self.token, "pid": os.getpid()})
                    self.paths.append(path)
            if data.get("type") == "control_cancel_request":
                self._finish(data.get("request_id"))
            request = data.get("request")
            request_id = data.get("request_id")
            if (data.get("type") != "control_request" or not isinstance(request, dict)
                    or request.get("subtype") != "can_use_tool" or not self.session
                    or not isinstance(request_id, str) or not request_id or request_id in self.finished):
                return
            if not isinstance(request.get("input"), dict) or not isinstance(request.get("tool_name"), str):
                return
            if len(json.dumps(request).encode()) > MAX_MESSAGE // 2 or len(self.pending) >= 64:
                return
            self.pending[request_id] = {"request_id": request_id, "session": self.session,
                                        "fingerprint": fingerprint(request), "request": request,
                                        "actionable": request["tool_name"] in APPROVABLE_TOOLS}

    def forward_input(self, raw):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = None
        with self.lock:
            if isinstance(data, dict) and data.get("type") == "control_response":
                response = data.get("response") or {}
                request_id = response.get("request_id") if isinstance(response, dict) else None
                if request_id in self.finished and self.finished[request_id]:
                    return  # The widget already answered this exact request.
                if isinstance(request_id, str):
                    self._finish(request_id)
            self.send_child(raw)

    def handle(self, message):
        if not isinstance(message, dict) or not secrets.compare_digest(str(message.get("token", "")), self.token):
            return {"ok": False, "error": "Permission connection changed."}
        with self.lock:
            if message.get("session") != self.session:
                return {"ok": False, "error": "Session changed."}
            if message.get("action") == "list":
                task_id = message.get("task_id") or None
                rows = [row for row in self.pending.values()
                        if (row["request"].get("agent_id") or "").removeprefix("agent-") == (task_id or "")]
                return {"ok": True, "pending": rows[:1]}
            decision = message.get("action")
            item = self.pending.get(message.get("request_id"))
            if (decision not in ("allow", "deny") or not item or not item["actionable"]
                    or message.get("fingerprint") != item["fingerprint"]):
                return {"ok": False, "error": "This request has changed or was already answered."}
            request = item["request"]
            response = {"behavior": decision, "toolUseID": request.get("tool_use_id")}
            if decision == "allow":
                response["updatedInput"] = request["input"]
            else:
                from .branding import APP_NAME
                response["message"] = "The user denied this request in " + APP_NAME + "."
            request_id = item["request_id"]
            self._finish(request_id, local=True)
            self.send_child(json.dumps({"type": "control_response", "response": {
                "subtype": "success", "request_id": request_id, "response": response}}).encode() + b"\n")
            # Dismiss the matching VS Code dialog; ignore its resulting late reply.
            self.send_client(json.dumps({"type": "control_cancel_request", "request_id": request_id}).encode() + b"\n")
            return {"ok": True}

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        for path in self.paths:
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("token") == self.token:
                    path.unlink()
            except (OSError, ValueError):
                pass
        if self.temp:
            self.temp.cleanup()


def permission_call(directory, session, message):
    try:
        endpoint = json.loads(endpoint_path(directory, session).read_text(encoding="utf-8"))
        if endpoint.get("session") != session:
            raise ValueError("Session mismatch")
        message = dict(message, session=session, token=endpoint["token"])
        if "port" in endpoint:
            port = endpoint["port"]
            if type(port) is not int or not 0 < port < 65536:
                raise ValueError("Invalid relay port")
            family, address = socket.AF_INET, ("127.0.0.1", port)
        else:
            family, address = socket.AF_UNIX, endpoint["socket"]
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(.3)
            connection.connect(address)
            connection.sendall(json.dumps(message).encode() + b"\n")
            with connection.makefile("rb") as stream:
                raw = stream.readline(MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE:
            raise ValueError("Response too large")
        answer = json.loads(raw)
        return answer if isinstance(answer, dict) else {"ok": False}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"ok": False, "error": "The permission connection is unavailable. Open the chat to answer there."}


def pending_permissions(directory, agent):
    if sys.platform not in ("darwin", "win32"):
        return []
    session = (agent.get("root_parent") or agent.get("parent")) if agent.get("sub") else agent.get("id")
    if not session or agent.get("state") == "closed":
        return []
    task_id = agent.get("id", "").removeprefix("agent-") if agent.get("sub") else None
    answer = permission_call(directory, session, {"action": "list", "task_id": task_id})
    rows = answer.get("pending", []) if answer.get("ok") else []
    # Child requests stay with their own row when Claude supplies an agent ID.
    return [row for row in rows if (row.get("request", {}).get("agent_id") or "").removeprefix("agent-") == (task_id or "")]


def answer_permission(directory, item, decision):
    return permission_call(directory, item["session"], {"action": decision,
        "request_id": item["request_id"], "fingerprint": item["fingerprint"]})
