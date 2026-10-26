"""Browser checks against the built site, HTTPS Nginx and the real central API."""

import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from family_controls.adapters.api import create_app
from family_controls.adapters.backend_sqlite import BackendStore
from family_controls.adapters.oidc import OIDC
from family_controls.adapters.passwords import ArgonPasswords
from family_controls.adapters.sqlite import Repository
from family_controls.adapters.timekpr import hour_map, validate_user
from family_controls.agent import AgentSync
from family_controls.application import Service
from family_controls.backend.accounts import Accounts
from family_controls.backend.models import BackendConfig, Enrollment
from family_controls.backend.service import Backend
from family_controls.clock import SystemClock
from family_controls.config import AgentConfig
from family_controls.domain import Observation
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1]
PASSWORD = "web-test-password-only"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Enforcement:
    """Deterministic device port; OS enforcement has its own VM suite."""

    def __init__(self, clock):
        self.clock = clock
        self.policies = {}
        self.used = {"mia": 55 * 60, "lina": 30 * 60}

    def validate(self, policy):
        hour_map(policy.windows, policy.homework)

    def apply(self, user, policy, now):
        self.policies[user] = policy

    def observe(self, user):
        policy = self.policies.get(user)
        limit = policy.limit_seconds if policy else 0
        return Observation(
            max(0, (86400 if limit is None else limit) - self.used.get(user, 0)),
            86400,
            self.clock.now(),
            {"playSeconds": self.used.get(user, 0), "activeActivities": 0},
        )


@pytest.fixture
def household(tmp_path, request):
    backend_port, web_port, listener_port = port(), port(), port()
    origin = f"https://127.0.0.1:{web_port}"
    password = tmp_path / "password"
    password.write_text(PASSWORD)
    policy = {
        "playTime": {"dailyLimitMinutes": 90, "activities": ["test-game"]},
        "timeWindows": {"allowed": {"ALL": ["00:00-24:00"]}},
    }
    config = BackendConfig.model_validate(
        {
            "publicUrl": origin,
            "authentication": {
                "bootstrapPasswordFile": str(password),
                "password": {"registration": True, "login": True},
            },
            "children": {
                name: {
                    "assignments": [
                        {"device": "jonas-pc" if name == "jonas" else "shared-pc", "user": name}
                    ],
                    "policy": policy,
                }
                for name in ("mia", "jonas", "lina")
            },
        }
    )

    class DaytimeClock(SystemClock):
        """Keep time-of-day controls deterministic, including late-night test runs."""

        def __init__(self):
            super().__init__("Europe/Berlin")
            now = super().now()
            self.offset = now.replace(hour=12, minute=0, second=0, microsecond=0) - now

        def now(self) -> datetime:
            return super().now() + self.offset

    clock = DaytimeClock()
    store = BackendStore(str(tmp_path / "backend.sqlite"))
    request.addfinalizer(store.close)
    backend = Backend(config, store, clock, validate_user)
    accounts = Accounts(config.authentication, store, ArgonPasswords(), clock)
    oidc = OIDC(config, store, clock, accounts)
    app = create_app(backend, accounts, oidc)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=backend_port, log_level="error", timeout_graceful_shutdown=1
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def stop_server():
        server.should_exit = True
        thread.join(timeout=5)

    request.addfinalizer(stop_server)
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    agent_config = AgentConfig.model_validate(
        {
            "timezone": "Europe/Berlin",
            "agent": {"deviceId": "shared-pc", "stateDir": str(tmp_path / "agent")},
            "managedUsers": {name: policy for name in ("mia", "lina")},
        }
    )
    repository = Repository(str(tmp_path / "agent.sqlite"))
    request.addfinalizer(repository.close)
    service = Service(agent_config, repository, Enforcement(clock), clock)
    credential = "device-browser-integration-secret-0000000000"
    token = backend.enrollment_token("shared-pc", "admin")
    backend.enroll(Enrollment(device="shared-pc", token=token, credential=credential))
    backend.device_decision("shared-pc", True, "admin")
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{backend_port}",
        headers={"Authorization": f"Bearer {credential}"},
        trust_env=False,
    )
    request.addfinalizer(client.close)

    class AgentClient:
        def request(self, route, payload):
            response = client.post(route, json=payload)
            response.raise_for_status()
            return response.json()

    sync = AgentSync(service, repository, AgentClient())
    sync.exchange()
    sync.exchange()
    runtime = tmp_path / "web-config.json"
    metadata = {
        "password": {"login": True, "registration": True},
        "oidc": {"login": False, "registration": False},
        "providers": [],
    }
    runtime.write_text(json.dumps(metadata))
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    snippet = (WEB / "nginx-locations.conf").read_text()
    for placeholder, value in {
        "assets": os.environ.get("WEB_BUILD", str(WEB / "build")),
        "runtime": str(runtime),
        "backend": f"http://127.0.0.1:{backend_port}",
        "ca": str(cert),
    }.items():
        snippet = snippet.replace(f"@{placeholder}@", value)
    nginx_config = tmp_path / "nginx.conf"
    nginx_config.write_text(f"""
daemon off;
pid {tmp_path}/nginx.pid;
error_log {tmp_path}/nginx-error.log;
events {{ worker_connections 256; }}
http {{
  access_log off;
  client_body_temp_path {tmp_path}/body;
  proxy_temp_path {tmp_path}/proxy;
  fastcgi_temp_path {tmp_path}/fastcgi;
  uwsgi_temp_path {tmp_path}/uwsgi;
  scgi_temp_path {tmp_path}/scgi;
  types {{ text/html html; text/css css; application/javascript js; application/json json; }}
  server {{
    listen 127.0.0.1:{web_port} ssl;
    server_name 127.0.0.1;
    ssl_certificate {cert}; ssl_certificate_key {key};
    location / {{
      proxy_pass http://127.0.0.1:{listener_port};
      proxy_set_header Host $http_host;
      proxy_buffering off;
    }}
  }}
  server {{
    listen 127.0.0.1:{listener_port};
    server_name 127.0.0.1;
    {snippet}
  }}
}}
""")
    nginx_binary = os.environ.get(
        "NGINX", shutil.which("nginx") or "/tmp/family-web-nginx/bin/nginx"
    )
    nginx = subprocess.Popen(
        [nginx_binary, "-p", str(tmp_path), "-c", str(nginx_config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    def stop_nginx():
        nginx.terminate()
        nginx.communicate(timeout=5)

    request.addfinalizer(stop_nginx)
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", web_port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    assert nginx.poll() is None, nginx.communicate()[1].decode()
    admin = httpx.Client(
        base_url=origin,
        verify=ssl.create_default_context(cafile=str(cert)),
        headers={"Authorization": f"Bearer {accounts.login('admin', PASSWORD)}"},
        trust_env=False,
    )
    request.addfinalizer(admin.close)
    yield SimpleNamespace(
        origin=origin,
        backend=backend,
        accounts=accounts,
        config=config,
        store=store,
        service=service,
        sync=sync,
        runtime=runtime,
        metadata=metadata,
        api=admin,
        oidc=oidc,
        cert=cert,
    )


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM", shutil.which("chromium")), headless=True
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(
        ignore_https_errors=True, locale="en-GB", viewport={"width": 1440, "height": 1000}
    )
    page = context.new_page()
    coverage = context.new_cdp_session(page) if os.environ.get("WEB_COVERAGE") else None
    if coverage:
        coverage.send("Profiler.enable")
        coverage.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
    page.set_default_timeout(6000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    if coverage:
        result = coverage.send("Profiler.takePreciseCoverage")
        destination = WEB / "coverage" / "raw"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{uuid.uuid4()}.json").write_text(json.dumps(result["result"]))
        coverage.send("Profiler.stopPreciseCoverage")
    context.close()
    assert not errors, errors


@pytest.fixture
def signed_in(page, household):
    page.goto(household.origin)
    page.get_by_label("Username", exact=True).fill("admin")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("heading", name="Today’s family plan").wait_for()
    return page
