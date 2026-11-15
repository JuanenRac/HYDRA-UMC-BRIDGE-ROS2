# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - tests/test_server_identity.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The identity of the SERVER as this service carries it (the shared module, byte for byte), against a real server on a free port."""

from __future__ import annotations

import base64
import hmac
import http.client
import json
import tempfile
import threading
import time
import unittest
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hydra_umc_bridge_ros2 import server_identity

KEY = b"a-signing-key-of-the-server-0123456789"
OWN_TOKEN = "own-token-of-the-service"


IDENTITY = server_identity


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(claims: dict, key: bytes = KEY, alg: str = "HS256", signature: bytes | None = None) -> str:
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = signature if signature is not None else hmac.new(key, f"{head}.{body}".encode(), sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


def good_claims(role: str = "admin", **extra) -> dict:
    return {"username": "ana", "role": role, "exp": int(time.time()) + 600, **extra}


class VerifyTests(unittest.TestCase):
    def test_a_good_token_gives_the_user_and_the_role(self) -> None:
        identity = IDENTITY.verify_token(make_token(good_claims("operator")), KEY)
        self.assertEqual((identity.username, identity.role), ("ana", "operator"))

    def test_every_defect_is_refused_with_a_reason(self) -> None:
        now = time.time()
        cases = {
            "none algorithm": make_token(good_claims(), alg="none"),
            "other algorithm": make_token(good_claims(), alg="HS512"),
            "wrong key": make_token(good_claims(), key=b"another-key-of-more-than-32-characters!"),
            "bad signature": make_token(good_claims(), signature=b"x" * 32),
            "expired": make_token({**good_claims(), "exp": int(now) - 5}),
            "no expiry": make_token({"username": "ana", "role": "admin"}),
            "boolean expiry": make_token({**good_claims(), "exp": True}),
            "not yet": make_token(good_claims(nbf=int(now) + 300)),
            "expiry that is not a number": make_token({**good_claims(), "exp": float("nan")}),
            "expiry without end": make_token({**good_claims(), "exp": float("inf")}),
            "issued in the future": make_token(good_claims(iat=int(now) + 300)),
            "no user": make_token({**good_claims(), "username": ""}),
            "unknown role": make_token(good_claims("root")),
            "not a token": "abc",
            "empty parts": "..",
            "too long": "a." * 3000,
            "": "",
        }
        for label, token in cases.items():
            with self.subTest(label), self.assertRaises(IDENTITY.IdentityError):
                IDENTITY.verify_token(token, KEY)

    def test_the_reasons_name_no_secret(self) -> None:
        with self.assertRaises(IDENTITY.IdentityError) as caught:
            IDENTITY.verify_token(make_token(good_claims(), key=b"another-key-of-more-than-32-characters!"), KEY)
        self.assertNotIn(KEY.decode(), str(caught.exception))


class KeyTests(unittest.TestCase):
    def test_the_key_comes_from_a_file_and_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            good, short = Path(folder, "good.key"), Path(folder, "short.key")
            good.write_text(KEY.decode() + "\n", encoding="utf-8")
            short.write_text("too-short", encoding="utf-8")
            self.assertEqual(IDENTITY.load_identity_key(str(good)), KEY)
            with self.assertRaises(ValueError) as caught:
                IDENTITY.load_identity_key(str(short))
            self.assertNotIn("too-short", str(caught.exception))
            with self.assertRaises(ValueError):
                IDENTITY.load_identity_key(str(Path(folder, "missing.key")))

    def test_off_unless_a_file_is_named(self) -> None:
        self.assertIsNone(IDENTITY.identity_key_from(None, {}))
        self.assertIsNone(IDENTITY.identity_key_from("", {IDENTITY.ENV_IDENTITY_KEY_FILE: "  "}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "k")
            path.write_text(KEY.decode(), encoding="utf-8")
            self.assertEqual(IDENTITY.identity_key_from(str(path), {}), KEY)
            self.assertEqual(IDENTITY.identity_key_from(None, {IDENTITY.ENV_IDENTITY_KEY_FILE: str(path)}), KEY)
        self.assertEqual(IDENTITY.ENV_IDENTITY_KEY_FILE, "HYDRA_UMC_BRIDGE_ROS2_IDENTITY_KEY_FILE")


class Handler(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        server = self.server
        if IDENTITY.accepts(self, server.own_token, server.key):  # type: ignore[attr-defined]
            who = getattr(self, "identity", None)
            body = json.dumps({"ok": True, "user": who.username if who else None}).encode()
            self.send_response(200)
        else:
            body = b'{"error": "a valid bearer token is required"}'
            self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args) -> None:
        pass


class ServerTests(unittest.TestCase):
    def start(self, own_token: str, key: bytes | None) -> int:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.own_token, server.key = own_token, key  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        return server.server_address[1]

    @staticmethod
    def call(port: int, method: str = "GET", token: str | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        connection.request(method, "/x", body=b"{}" if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode())
        connection.close()
        return response.status, data

    def test_off_nothing_changes(self) -> None:
        open_port = self.start("", None)
        self.assertEqual(self.call(open_port)[0], 200)
        token_port = self.start(OWN_TOKEN, None)
        self.assertEqual(self.call(token_port)[0], 401)
        self.assertEqual(self.call(token_port, token=OWN_TOKEN)[0], 200)
        # a token of the SERVER means nothing while the identity is off
        self.assertEqual(self.call(token_port, token=make_token(good_claims()))[0], 401)

    def test_on_a_service_without_a_token_of_its_own_is_closed_to_anybody_without_credentials(self) -> None:
        port = self.start("", KEY)
        self.assertEqual(self.call(port)[0], 401)
        self.assertEqual(self.call(port, token="whatever")[0], 401)
        status, data = self.call(port, token=make_token(good_claims("viewer")))
        self.assertEqual((status, data["user"]), (200, "ana"))

    def test_on_a_viewer_reads_but_does_not_change(self) -> None:
        port = self.start(OWN_TOKEN, KEY)
        viewer = make_token(good_claims("viewer"))
        self.assertEqual(self.call(port, "GET", viewer)[0], 200)
        self.assertEqual(self.call(port, "POST", viewer)[0], 401)
        for role in ("operator", "admin"):
            self.assertEqual(self.call(port, "POST", make_token(good_claims(role)))[0], 200)

    def test_on_the_own_token_still_works_and_a_forged_token_does_not(self) -> None:
        port = self.start(OWN_TOKEN, KEY)
        self.assertEqual(self.call(port, "POST", OWN_TOKEN), (200, {"ok": True, "user": None}))
        forged = make_token(good_claims(), key=b"another-key-of-more-than-32-characters!")
        self.assertEqual(self.call(port, "GET", forged)[0], 401)
        self.assertEqual(self.call(port, "GET", make_token({**good_claims(), "exp": 1}))[0], 401)

    def test_the_probe_of_a_container_signs_its_own_viewer_token(self) -> None:
        port = self.start(OWN_TOKEN, KEY)
        with tempfile.TemporaryDirectory() as folder:
            good, bad = Path(folder, "good.key"), Path(folder, "bad.key")
            good.write_text(KEY.decode(), encoding="utf-8")
            bad.write_text("another-key-of-more-than-32-characters!", encoding="utf-8")
            self.assertTrue(IDENTITY.probe(port, "/health", str(good)))
            self.assertFalse(IDENTITY.probe(port, "/health", str(bad)))
            self.assertEqual(IDENTITY.main(["probe", "--port", str(port), "--key-file", str(good)]), 0)
            self.assertEqual(IDENTITY.main(["probe", "--port", str(port), "--key-file", str(bad)]), 1)
        claims = json.loads(base64.urlsafe_b64decode(IDENTITY.sign_token(KEY).split(".")[1] + "=="))
        self.assertEqual((claims["role"], claims["username"]), ("viewer", "health-probe"))
        self.assertLessEqual(claims["exp"] - time.time(), 61)

    def test_the_probe_with_a_missing_key_file_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(IDENTITY.main(["probe", "--port", "9", "--key-file", str(Path(folder, "missing.key"))]), 2)


if __name__ == "__main__":
    unittest.main()
