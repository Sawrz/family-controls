import pathlib
import sys
import unittest
import urllib.error
from unittest import mock


SRC_DIR = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import family_controls_relay as relay_module  # noqa: E402


def reachable_status(**overrides):
    status = {
        "phase": "allowed",
        "insideUnmeteredWindow": False,
        "insideAllowedWindow": True,
        "remainingAllowedSeconds": 600,
        "summary": {"playTimeLeft": "15m", "allowedUntil": "18:00"},
        "effectiveDailyLimitMinutes": 60,
        "pendingRequest": None,
        "muted": False,
        "blocked": False,
    }
    status.update(overrides)
    return status


class CapturingRelay(relay_module.Relay):
    def __init__(self):
        super().__init__(
            {
                "parentGroupChatId": "-100",
                "hostChildren": {
                    "lilly": {
                        "host": "lilly.local",
                        "port": 18451,
                        "user": "lilly",
                        "deviceId": "lilly-device",
                    },
                    "lucy": {
                        "host": "lucy.local",
                        "port": 18451,
                        "user": "lucy",
                        "deviceId": "lucy-device",
                    },
                },
            },
            "bot-token",
        )
        self.telegram_calls = []

    def telegram(self, method, payload):
        self.telegram_calls.append((method, payload))
        if method == "getChatMember":
            return {"result": {"status": "administrator"}}
        if method == "sendMessage":
            return {"result": {"message_id": len(self.telegram_calls)}}
        return {"ok": True}


class FamilyControlsRelayTests(unittest.TestCase):
    def test_all_statuses_markdown_renders_reachable_and_unreachable_children(self):
        relay = CapturingRelay()
        calls = []

        def fake_http_request(url, **kwargs):
            calls.append((url, kwargs))
            if "lilly.local" in url:
                return reachable_status()
            raise urllib.error.URLError(TimeoutError("timed out"))

        with mock.patch.object(relay_module, "http_request", side_effect=fake_http_request):
            markdown = relay.all_statuses_markdown()

        self.assertIn("*Lilly*", markdown)
        self.assertIn("Phase: `allowed`", markdown)
        self.assertIn("*Lucy*", markdown)
        self.assertIn("Status: `agent unreachable`", markdown)
        self.assertIn("Connection error: `timed out`", markdown)
        self.assertEqual([kwargs["timeout"] for _url, kwargs in calls], [5, 5])

    def test_sync_pending_requests_skips_unreachable_child_and_processes_reachable_child(self):
        relay = CapturingRelay()

        def fake_http_request(url, **_kwargs):
            if "lilly.local" in url:
                return reachable_status(
                    phase="pending_request",
                    pendingRequest={"requestedAt": "2026-07-01T12:00:00+02:00"},
                )
            raise OSError("network unreachable")

        with mock.patch.object(relay_module, "http_request", side_effect=fake_http_request):
            relay.sync_pending_requests()

        send_messages = [call for call in relay.telegram_calls if call[0] == "sendMessage"]
        self.assertEqual(len(send_messages), 1)
        self.assertIn("Lilly", send_messages[0][1]["text"])
        self.assertEqual(
            relay.pending_messages["lilly"]["requestedAt"], "2026-07-01T12:00:00+02:00"
        )
        self.assertNotIn("lucy", relay.pending_messages)

    def test_command_reports_unreachable_child_agent(self):
        relay = CapturingRelay()
        message = {
            "text": "/addtime lilly 5",
            "chat": {"id": "-100"},
            "from": {"id": 42},
        }

        with mock.patch.object(
            relay_module,
            "http_request",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            relay.handle_command(message)

        self.assertEqual(relay.telegram_calls[-1][0], "sendMessage")
        self.assertIn("Lilly agent is unreachable", relay.telegram_calls[-1][1]["text"])

    def test_callback_answers_unreachable_child_agent(self):
        relay = CapturingRelay()
        callback = {
            "id": "callback-id",
            "data": "quick:add-time:lilly:5",
            "message": {"message_id": 10, "chat": {"id": "-100"}},
            "from": {"id": 42},
        }

        with mock.patch.object(
            relay_module,
            "http_request",
            side_effect=TimeoutError("timed out"),
        ):
            relay.resolve_callback(callback)

        answer_calls = [call for call in relay.telegram_calls if call[0] == "answerCallbackQuery"]
        self.assertEqual(len(answer_calls), 1)
        self.assertTrue(answer_calls[0][1]["show_alert"])
        self.assertIn("Lilly agent is unreachable", answer_calls[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
