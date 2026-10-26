"""Responsive Tk adapter. All socket I/O runs off the UI thread."""

import argparse
import fcntl
import os
import socket
import threading
import time
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk
from typing import Any
from uuid import uuid4

from ..domain import duration, parse_duration
from ..notifications import Warnings
from .http import unix_request


class CountdownApp:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.root = tk.Tk(className="family-controls-countdown")
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.window: tk.Toplevel | None = None
        self.label_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        self.minutes_var = tk.StringVar(value="15m")
        self.reason_var = tk.StringVar()
        self.feedback = ""
        self.latest: dict = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="family-ui")
        self.future: Future | None = None
        self.request_button: ttk.Button | None = None
        self.cancel_button: ttk.Button | None = None
        self.closed = False
        self.next_poll = 0.0
        self.queued: tuple[str, dict, str] | None = None
        self.mutating = False
        self.warnings = Warnings()
        self.notifications: Any = None
        self.show_requested = threading.Event()
        self.visible_by_choice = False
        self.last_request: object = None

    def ensure_window(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            return
        window = tk.Toplevel(self.root, class_="family-controls-countdown")
        window.title("Family Controls")
        window.attributes("-topmost", True)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.destroy_window)
        frame = ttk.Frame(window, padding=16)
        frame.grid()
        ttk.Label(frame, text="Your screen time", font=("Sans", 18, "bold")).grid(sticky="w")
        ttk.Label(frame, textvariable=self.label_var, font=("Sans", 30, "bold")).grid(
            sticky="w", pady=8
        )
        ttk.Label(frame, textvariable=self.detail_var, wraplength=400).grid(sticky="w", pady=8)
        ttk.Label(frame, text="Request duration (for example 25m or 1h 10m)").grid(sticky="w")
        ttk.Entry(frame, textvariable=self.minutes_var).grid(sticky="ew")
        ttk.Label(frame, text="Reason (optional)").grid(sticky="w")
        ttk.Entry(frame, textvariable=self.reason_var).grid(sticky="ew")
        self.request_button = ttk.Button(frame, text="Request more time", command=self.request_time)
        self.request_button.grid(sticky="w", pady=8)
        self.cancel_button = ttk.Button(frame, text="Cancel request", command=self.cancel_request)
        self.cancel_button.grid(sticky="w")
        self.window = window

    def destroy_window(self) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.visible_by_choice = False

    def submit(self, route: str, payload: dict) -> None:
        if self.mutating or self.queued:
            return
        self.queued = (route, payload, str(uuid4()))
        self.feedback = "Sending…"
        self.render(self.latest)

    def request_time(self) -> None:
        try:
            minutes = parse_duration(self.minutes_var.get())
        except ValueError as exc:
            self.feedback = str(exc)
            self.render(self.latest)
            return
        self.submit("/v1/request-time", {"minutes": minutes, "reason": self.reason_var.get()})

    def cancel_request(self) -> None:
        request = self.latest.get("pendingRequest")
        if request:
            self.submit("/v1/cancel-request", {"requestId": request["id"]})

    def render(self, status: dict) -> None:
        self.latest = status
        request = status.get("request")
        warning = self.warnings.observe(status)
        if warning is not None:
            self.visible_by_choice = True
            if self.notifications:
                self.executor.submit(self.notifications.notify, warning)
        if request != self.last_request:
            self.last_request = request
            if request is not None:
                self.visible_by_choice = True
        show = self.visible_by_choice or status.get("phase") in {"expired", "unknown"}
        if not show:
            self.destroy_window()
            return
        self.ensure_window()
        remaining = status.get("remainingCountdownSeconds")
        self.label_var.set(duration(remaining) if remaining is not None else "Unavailable")
        detail = status.get("phase", "unknown").replace("_", " ")
        if request:
            detail += f". Request {request['status']}"
            if request.get("decided_by"):
                detail += f" by {request['decided_by']}"
            if request["status"] == "denied" and request.get("decision"):
                detail += f". Reason: {request['decision']}"
        if status.get("muted"):
            detail += ". Requests are muted; ask a parent directly"
        if status.get("application", {}).get("status") == "pending":
            detail += ". Policy change is waiting to be applied"
        self.detail_var.set(detail + (". " + self.feedback if self.feedback else ""))
        if self.request_button and self.cancel_button:
            disabled = bool(
                status.get("muted")
                or status.get("blocked")
                or status.get("pendingRequest")
                or self.mutating
                or self.queued
            )
            self.request_button.state(["disabled"] if disabled else ["!disabled"])
            self.cancel_button.state(
                ["!disabled"]
                if status.get("pendingRequest") and not (self.mutating or self.queued)
                else ["disabled"]
            )

    def tick(self) -> None:
        if self.closed:
            return
        try:
            if self.show_requested.is_set():
                self.show_requested.clear()
                self.visible_by_choice = True
                self.render(self.latest)
                if self.window:
                    self.window.deiconify()
                    self.window.lift()
            if self.future is not None and self.future.done():
                future, self.future = self.future, None
                try:
                    status = future.result()
                    if self.mutating:
                        self.feedback = "Request updated."
                    self.mutating = False
                    self.render(status)
                except Exception as exc:
                    self.mutating = False
                    self.feedback = str(exc)
                    self.render({"phase": "unknown", "remainingCountdownSeconds": None})
            if self.future is None and self.queued:
                route, payload, operation = self.queued
                self.queued = None
                self.mutating = True
                self.future = self.executor.submit(
                    unix_request, self.socket_path, route, payload, operation
                )
                self.render(self.latest)
            elif self.future is None and time.monotonic() >= self.next_poll:
                self.next_poll = time.monotonic() + 2
                self.future = self.executor.submit(unix_request, self.socket_path, "/v1/status")
        finally:
            self.root.after(500, self.tick)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.notifications:
            self.notifications.close()
        self.destroy_window()
        self.request_button = self.cancel_button = None
        # Tk variables must be finalized on the UI thread, before cyclic garbage
        # collection can run in a later network/notification worker.
        del self.label_var, self.detail_var, self.minutes_var, self.reason_var
        self.root.destroy()
        del self.root

    def run(self) -> None:
        self.tick()
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-path", default="/run/family-controls/control.sock")
    parser.add_argument("--show", action="store_true", help="Open the request window immediately")
    args = parser.parse_args()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    address = str(runtime / "family-controls-ui.sock")
    with (runtime / "family-controls-ui.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if args.show:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(address)
                    client.sendall(b"show")
            return
        from .desktop_notifications import DesktopNotifications

        app = CountdownApp(args.socket_path)
        app.notifications = DesktopNotifications(app.show_requested.set)
        app.visible_by_choice = args.show
        Path(address).unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(address)
            Path(address).chmod(0o600)
            listener.listen(4)
            listener.settimeout(1)

            def listen() -> None:
                while not app.closed:
                    try:
                        connection, _ = listener.accept()
                        with connection:
                            connection.settimeout(1)
                            if connection.recv(4) == b"show":
                                app.show_requested.set()
                    except TimeoutError:
                        continue

            worker = threading.Thread(target=listen, daemon=True)
            worker.start()
            try:
                app.run()
            finally:
                app.closed = True
                worker.join(timeout=2)
                Path(address).unlink(missing_ok=True)
