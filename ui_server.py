#!/usr/bin/env python3
"""
Docksmith Web UI Server
Provides a REST API for the browser-based dashboard.
"""

import sys
import os
import json
import subprocess
import threading
import queue
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docksmith.state import DocksmithState
from docksmith.image_store import ImageStore

try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse as urlparse
except ImportError:
    pass


state = DocksmithState()
store = ImageStore(state)

# Global log stream for live build output
build_logs = {}  # build_id -> list of log lines
build_status = {}  # build_id -> "running" | "done" | "error"


class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default access logs

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, status=200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type="text/html"):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/" or path == "/index.html":
            ui_path = os.path.join(os.path.dirname(__file__), "ui", "index.html")
            self.send_file(ui_path, "text/html")

        elif path == "/api/images":
            images = store.list_images()
            result = []
            for img in images:
                digest = img.get("digest", "")
                short_id = digest.split(":")[-1][:12] if ":" in digest else digest[:12]
                total_size = sum(l.get("size", 0) for l in img.get("layers", []))
                result.append({
                    "name": img["name"],
                    "tag": img["tag"],
                    "id": short_id,
                    "digest": digest,
                    "created": img.get("created", ""),
                    "layers": len(img.get("layers", [])),
                    "size": total_size,
                    "config": img.get("config", {})
                })
            self.send_json(result)

        elif path.startswith("/api/image/"):
            # /api/image/<name>/<tag>
            parts = path[len("/api/image/"):].split("/")
            if len(parts) >= 2:
                name, tag = parts[0], parts[1]
                try:
                    img = store.get_image(name, tag)
                    self.send_json(img)
                except FileNotFoundError:
                    self.send_json({"error": f"Image {name}:{tag} not found"}, 404)
            else:
                self.send_json({"error": "Bad request"}, 400)

        elif path.startswith("/api/build/status/"):
            build_id = path[len("/api/build/status/"):]
            logs = build_logs.get(build_id, [])
            status = build_status.get(build_id, "unknown")
            self.send_json({"status": status, "logs": logs})

        elif path == "/api/state":
            layers_dir = state.layers_dir
            images_dir = state.images_dir
            total_layers = len([f for f in os.listdir(layers_dir) if f.endswith(".tar")])
            total_images = len([f for f in os.listdir(images_dir) if f.endswith(".json")])
            
            # Total size of layers
            total_size = 0
            for f in os.listdir(layers_dir):
                if f.endswith(".tar"):
                    total_size += os.path.getsize(os.path.join(layers_dir, f))

            cache_index = state.load_cache_index()
            self.send_json({
                "root": state.root,
                "total_images": total_images,
                "total_layers": total_layers,
                "total_size_bytes": total_size,
                "cache_entries": len(cache_index)
            })

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if path == "/api/build":
            # Start async build
            import uuid
            build_id = str(uuid.uuid4())[:8]
            build_logs[build_id] = []
            build_status[build_id] = "running"

            tag = data.get("tag", "myapp:latest")
            context = data.get("context", ".")
            no_cache = data.get("no_cache", False)

            def run_build():
                try:
                    cmd = [sys.executable, "docksmith_cli.py", "build", "-t", tag, context]
                    if no_cache:
                        cmd.insert(-1, "--no-cache")

                    proc = subprocess.Popen(
                        cmd,
                        cwd=os.path.dirname(__file__),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )
                    for line in proc.stdout:
                        build_logs[build_id].append(line.rstrip())
                    proc.wait()
                    if proc.returncode == 0:
                        build_status[build_id] = "done"
                    else:
                        build_status[build_id] = "error"
                except Exception as e:
                    build_logs[build_id].append(f"ERROR: {e}")
                    build_status[build_id] = "error"

            t = threading.Thread(target=run_build, daemon=True)
            t.start()
            self.send_json({"build_id": build_id})

        elif path == "/api/run":
            name_tag = data.get("name_tag", "")
            cmd_override = data.get("cmd", "")
            env_overrides = data.get("env", {})

            if not name_tag:
                self.send_json({"error": "name_tag required"}, 400)
                return

            run_cmd = [sys.executable, "docksmith_cli.py", "run"]
            for k, v in env_overrides.items():
                run_cmd.extend(["-e", f"{k}={v}"])
            run_cmd.append(name_tag)
            if cmd_override:
                import shlex
                run_cmd.extend(shlex.split(cmd_override))

            try:
                result = subprocess.run(
                    run_cmd,
                    cwd=os.path.dirname(__file__),
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                self.send_json({
                    "exit_code": result.returncode,
                    "output": result.stdout + result.stderr
                })
            except subprocess.TimeoutExpired:
                self.send_json({"error": "Container timed out"}, 408)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/import":
            try:
                result = subprocess.run(
                    [sys.executable, "scripts/import_base_image.py"],
                    cwd=os.path.dirname(__file__),
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                self.send_json({
                    "success": result.returncode == 0,
                    "output": result.stdout + result.stderr
                })
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/image/"):
            parts = path[len("/api/image/"):].split("/")
            if len(parts) >= 2:
                name, tag = parts[0], parts[1]
                try:
                    store.remove_image(name, tag)
                    self.send_json({"success": True, "message": f"Removed {name}:{tag}"})
                except FileNotFoundError as e:
                    self.send_json({"error": str(e)}, 404)
                except Exception as e:
                    self.send_json({"error": str(e)}, 500)
            else:
                self.send_json({"error": "Bad request"}, 400)
        else:
            self.send_json({"error": "Not found"}, 404)


def main():
    port = int(os.environ.get("DOCKSMITH_UI_PORT", "7474"))
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    print(f"┌{'─'*50}┐")
    print(f"│  Docksmith UI Server                             │")
    print(f"│  http://localhost:{port}                          │")
    print(f"│  Press Ctrl+C to stop                           │")
    print(f"└{'─'*50}┘")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
