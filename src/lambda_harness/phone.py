"""Private phone access through a temporary Tailscale Serve session."""

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from email.header import decode_header, make_header
from pathlib import Path

from .process import run_process


class PhoneAccess:
    def __init__(self, executable, hostname, login, port=443):
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+ts\.net", hostname):
            raise ValueError("Tailscale did not provide a valid device DNS name")
        if not isinstance(login, str) or not login or any(ord(c) < 32 for c in login):
            raise ValueError("Sign in to Tailscale with a user account to enable phone access")
        if not 1 <= port <= 65535:
            raise ValueError("Tailscale port must be between 1 and 65535")
        self.executable, self.hostname, self.login, self.port = executable, hostname, login, port
        self.host = hostname if port == 443 else f"{hostname}:{port}"
        self.origin = f"https://{self.host}"
        self.process = self.output = None
        self.env = {**os.environ, "TAILSCALE_BE_CLI": "1"}

    @classmethod
    def discover(cls, port=443, *, executable=None):
        if executable is None:
            executable = shutil.which("tailscale")
            mac_app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
            if not executable and mac_app.is_file():
                executable = str(mac_app)
        if not executable:
            raise ValueError("Install Tailscale and sign in before using --tailscale")
        status = cls._json(executable, ["status", "--json"])
        own = status.get("Self") or {}
        if status.get("BackendState") != "Running" or own.get("Tags"):
            raise ValueError("Connect Tailscale using your user account before using --tailscale")
        login = (status.get("User") or {}).get(str(own.get("UserID")), {}).get("LoginName")
        return cls(executable, own.get("DNSName", "").rstrip(".").lower(), login, port)

    @staticmethod
    def _json(executable, args):
        code, out, err = run_process(
            [executable, *args],
            Path.cwd(),
            timeout=10,
            env={**os.environ, "TAILSCALE_BE_CLI": "1"},
        )
        if code:
            raise RuntimeError(f"Tailscale: {(err or out).strip()}")
        try:
            result = json.loads(out) or {}
        except ValueError as error:
            raise RuntimeError("Tailscale returned an unreadable status") from error
        if not isinstance(result, dict):
            raise RuntimeError("Tailscale returned an unexpected status")
        return result

    def _configs(self):
        config = self._json(self.executable, ["serve", "status", "--json"])
        return [config, *(config.get("Foreground") or {}).values()]

    def start(self, local_port, *, timeout=20):
        if any(
            str(self.port) in (c.get("TCP") or {})
            or (c.get("AllowFunnel") or {}).get(f"{self.hostname}:{self.port}")
            for c in self._configs()
        ):
            raise ValueError(
                f"Tailscale port {self.port} is already in use. "
                "Choose another with --tailscale-port; existing services were kept."
            )
        target = f"http://127.0.0.1:{local_port}"
        self.output = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                [self.executable, "serve", f"--https={self.port}", target],
                stdin=subprocess.DEVNULL,
                stdout=self.output,
                stderr=subprocess.STDOUT,
                env=self.env,
            )
            deadline = time.monotonic() + timeout
            while self.process.poll() is None and time.monotonic() < deadline:
                for config in self._configs():
                    handlers = (
                        (config.get("Web") or {})
                        .get(f"{self.hostname}:{self.port}", {})
                        .get("Handlers", {})
                    )
                    if (
                        handlers.get("/", {}).get("Proxy") == target
                        and (config.get("TCP") or {}).get(str(self.port), {}).get("HTTPS")
                        and not any((config.get("AllowFunnel") or {}).values())
                    ):
                        return
                time.sleep(0.1)
            self.output.seek(0)
            detail = self.output.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "Could not enable phone access. " + (detail or "Tailscale Serve timed out.")
            )
        except BaseException:
            self.close()
            raise

    def info(self):
        return {
            "url": self.origin,
            "ready": self.process is not None and self.process.poll() is None,
        }

    def close(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except ProcessLookupError:
                self.process.wait()
        if self.output is not None:
            self.output.close()
            self.output = None


def request_origin(headers, local_host, phone):
    """Forwarded requests must never fall through to the local browser's access."""
    forwarded = False
    for key in headers:
        lower = key.lower()
        proxy_header = lower == "forwarded" or lower.startswith(("x-forwarded-", "tailscale-"))
        forwarded |= proxy_header
        if (proxy_header or lower in {"host", "origin", "x-harness-token"}) and len(
            headers.get_all(key)
        ) != 1:
            raise ValueError("Repeated security headers are not allowed")
    host = headers.get("Host")
    if not forwarded and host == local_host:
        expected = f"http://{local_host}"
    else:
        if (
            phone is None
            or host != phone.host
            or headers.get("X-Forwarded-Host") != phone.host
            or headers.get("X-Forwarded-Proto") != "https"
            or headers.get("Tailscale-Funnel-Request") is not None
        ):
            raise ValueError("Use the local URL or the configured private Tailscale URL")
        try:
            login = str(make_header(decode_header(headers.get("Tailscale-User-Login", ""))))
        except (ValueError, LookupError) as error:
            raise ValueError("Invalid Tailscale identity") from error
        if not secrets.compare_digest(login.encode(), phone.login.encode()):
            raise ValueError(
                "Phone access is limited to the Tailscale account running this harness"
            )
        expected = phone.origin
    if headers.get("Origin") is not None and headers["Origin"] != expected:
        raise ValueError("Foreign origins are not allowed")
    return expected
