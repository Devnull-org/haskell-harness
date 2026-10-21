"""Phone access owns one temporary Serve session and checks proxy identity."""

import json
from email.message import Message

import pytest

from lambda_harness.phone import PhoneAccess, request_origin

from .helpers import executable


def fake_tailscale(tmp_path, *, busy=False, failure=False):
    config = tmp_path / "serve.json"
    initial = {"TCP": {"11434": {"TCPForward": "localhost:11434"}}}
    if busy:
        initial["TCP"]["443"] = {"HTTPS": True}
    config.write_text(json.dumps(initial))
    cli = executable(
        tmp_path,
        f"""
import json, os, signal, sys, time
from pathlib import Path
assert os.environ['TAILSCALE_BE_CLI'] == '1'
path = Path({str(config)!r})
args = sys.argv[1:]
if args == ['status', '--json']:
    print(json.dumps({{
        'BackendState':'Running',
        'Self':{{'DNSName':'laptop.example.ts.net.', 'UserID':1}},
        'User':{{'1':{{'LoginName':'owner@example.com'}}}}
    }}))
elif args == ['serve', 'status', '--json']:
    print(path.read_text())
else:
    assert args == ['serve', '--https=443', 'http://127.0.0.1:8765'], args
    if {failure!r}:
        print('Enable HTTPS at https://login.tailscale.com/admin/dns', flush=True)
        sys.exit(1)
    original = json.loads(path.read_text())
    config = {{**original, 'Foreground':{{'owned-session':{{
        'TCP':{{'443':{{'HTTPS':True}}}},
        'Web':{{'laptop.example.ts.net:443':{{'Handlers':{{'/':{{'Proxy':args[-1]}}}}}}}}
    }}}}}}
    path.write_text(json.dumps(config))
    def stop(*args):
        path.write_text(json.dumps(original))
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while True: time.sleep(.1)
""",
    )
    return cli, config, initial


def test_phone_session_keeps_existing_services_and_removes_only_its_own_proxy(tmp_path):
    cli, config, before = fake_tailscale(tmp_path)
    phone = PhoneAccess.discover(executable=cli)
    assert phone.origin == "https://laptop.example.ts.net"
    assert phone.login == "owner@example.com"
    try:
        phone.start(8765)
        assert phone.info() == {"url": phone.origin, "ready": True}
        assert json.loads(config.read_text())["TCP"] == before["TCP"]
    finally:
        phone.close()
    assert json.loads(config.read_text()) == before
    assert phone.info()["ready"] is False


def test_phone_refuses_an_existing_port_without_changing_it(tmp_path):
    cli, config, before = fake_tailscale(tmp_path, busy=True)
    phone = PhoneAccess.discover(executable=cli)
    with pytest.raises(ValueError, match="already in use"):
        phone.start(8765)
    assert json.loads(config.read_text()) == before


def test_phone_startup_errors_preserve_the_provider_instructions(tmp_path):
    cli, config, before = fake_tailscale(tmp_path, failure=True)
    phone = PhoneAccess.discover(executable=cli)
    with pytest.raises(RuntimeError, match="Enable HTTPS"):
        phone.start(8765)
    assert phone.info()["ready"] is False
    assert json.loads(config.read_text()) == before


def test_phone_timeout_stops_the_child_and_removes_its_temporary_proxy(tmp_path, monkeypatch):
    cli, config, before = fake_tailscale(tmp_path)
    phone = PhoneAccess.discover(executable=cli)
    monkeypatch.setattr(phone, "_configs", lambda: [before])
    with pytest.raises(RuntimeError, match="timed out"):
        phone.start(8765, timeout=0.2)
    assert phone.process.poll() is not None
    assert json.loads(config.read_text()) == before


@pytest.mark.parametrize(
    "status",
    [
        {"BackendState": "Stopped"},
        {"BackendState": "Running", "Self": {"Tags": ["tag:shared"]}},
        {"BackendState": "Running", "Self": {"DNSName": "bad.example", "UserID": 1}},
    ],
)
def test_phone_requires_a_connected_user_device_with_a_tailscale_name(status, monkeypatch):
    monkeypatch.setattr(PhoneAccess, "_json", lambda *args: status)
    with pytest.raises(ValueError):
        PhoneAccess.discover(executable="unused")


@pytest.mark.parametrize("header", ["Host", "Origin", "Tailscale-User-Login", "X-Forwarded-Host"])
def test_duplicate_security_headers_are_rejected(header):
    headers = Message()
    headers["Host"] = "127.0.0.1:8765"
    if header != "Host":
        headers[header] = "first"
    headers[header] = "second"
    with pytest.raises(ValueError):
        request_origin(headers, "127.0.0.1:8765", None)
