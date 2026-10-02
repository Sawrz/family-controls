#!/usr/bin/env python3
import argparse
import tkinter as tk
from tkinter import ttk

from common import format_duration, unix_request


class CountdownApp:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.root = tk.Tk()
        self.root.withdraw()
        self.label_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="")
        self.window: tk.Toplevel | None = None

    def ensure_window(self) -> None:
        if self.window is not None:
            return
        root = tk.Toplevel(self.root)
        root.title("Family Controls")
        root.tk.call(
            "wm", "class", str(root), "family-controls-countdown", "FamilyControlsCountdown"
        )
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.geometry("+40+40")
        frame = ttk.Frame(root, padding=16)
        frame.grid()
        ttk.Label(frame, text="Time is running out", font=("Sans", 18, "bold")).grid(sticky="w")
        ttk.Label(frame, textvariable=self.label_var, font=("Sans", 30, "bold")).grid(
            sticky="w", pady=(12, 4)
        )
        ttk.Label(frame, textvariable=self.detail_var, wraplength=360).grid(
            sticky="w", pady=(0, 12)
        )
        ttk.Button(frame, text="Request more time", command=self.request_time).grid(sticky="w")
        self.window = root

    def destroy_window(self) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def request_time(self) -> None:
        unix_request(self.socket_path, "POST", "/v1/request-time", {})

    def tick(self) -> None:
        try:
            status = unix_request(self.socket_path, "GET", "/v1/status")
        except Exception:
            self.destroy_window()
            self.schedule_next()
            return

        phase = status.get("phase")
        if phase in {"countdown_active", "pending_request", "muted"}:
            self.ensure_window()
            remaining = int(status.get("remainingCountdownSeconds", 0))
            self.label_var.set(format_duration(remaining))
            if phase == "pending_request":
                self.detail_var.set("Request pending. Waiting for a parent decision.")
            elif phase == "muted":
                self.detail_var.set("Requests are muted. Ask a parent directly.")
            else:
                self.detail_var.set(
                    "You can keep using the session until the countdown reaches zero."
                )
        else:
            self.destroy_window()

        self.schedule_next()

    def schedule_next(self) -> None:
        self.root.after(1000, self.tick)

    def run(self) -> None:
        self.tick()
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", default="/run/family-controls/control.sock")
    args = parser.parse_args()
    CountdownApp(args.socket_path).run()


if __name__ == "__main__":
    main()
