# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the client certificate (mutual TLS)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The settings, the file checks and a real handshake against a local TLS server that demands a client certificate.

The certificates are made in a temporary folder with the `openssl` command; the handshake tests skip themselves, saying why, when it is missing.
"""
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hydra_umc_bridge_ros2 import mtls
from hydra_umc_bridge_ros2.mtls import MutualTlsError, MutualTlsSettings

OPENSSL = shutil.which("openssl")


def run(*args, cwd):
    # a minimal configuration of its own: an inherited OPENSSL_CONF may point at a file that is not there
    conf = Path(cwd) / "openssl-test.cnf"
    conf.write_text("[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=test\n", encoding="utf-8")
    environment = dict(os.environ, OPENSSL_CONF=str(conf))
    subprocess.run([OPENSSL, *args], cwd=cwd, check=True, capture_output=True, env=environment)


def make_ca(folder: Path, name: str) -> None:
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.pem", "-days", "2", "-subj", f"/CN={name}",
        "-addext", "basicConstraints=critical,CA:TRUE", cwd=folder)


def make_leaf(folder: Path, ca: str, name: str, server: bool) -> None:
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr", "-subj", f"/CN={name}", cwd=folder)
    extension = folder / f"{name}.ext"
    extension.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n" if server else "extendedKeyUsage=clientAuth\n", encoding="utf-8")
    run("x509", "-req", "-in", f"{name}.csr", "-CA", f"{ca}.pem", "-CAkey", f"{ca}.key", "-CAcreateserial", "-out", f"{name}.pem", "-days", "2",
        "-extfile", extension.name, cwd=folder)


def settings_in(folder: Path, ca="ca", cert="client", key="client") -> MutualTlsSettings:
    if os.name != "nt":
        os.chmod(folder / f"{key}.key", 0o600)
    return MutualTlsSettings(True, str(folder / f"{ca}.pem"), str(folder / f"{cert}.pem"), str(folder / f"{key}.key"))


class SettingsTests(unittest.TestCase):
    def test_it_is_off_unless_the_switch_is_a_real_yes(self):
        for env in ({}, {mtls.ENV_SWITCH: "0"}, {mtls.ENV_SWITCH: ""}, {mtls.ENV_SWITCH: "maybe"}, {mtls.ENV_CA: "/x", mtls.ENV_CERT: "/y", mtls.ENV_KEY: "/z"}):
            self.assertEqual(mtls.settings_from_environment(env), MutualTlsSettings(), env)
        on = mtls.settings_from_environment({mtls.ENV_SWITCH: " Yes ", mtls.ENV_CA: "/a", mtls.ENV_CERT: "/b", mtls.ENV_KEY: "/c"})
        self.assertEqual(on, MutualTlsSettings(True, "/a", "/b", "/c"))

    def test_off_changes_nothing(self):
        self.assertIsNone(mtls.mqtt_module_for(MutualTlsSettings()))
        mtls.check_files(MutualTlsSettings())

    def test_a_missing_or_relative_path_is_refused(self):
        with self.assertRaisesRegex(MutualTlsError, "HYDRA_UMC_ROS2_MTLS_CA is needed"):
            mtls.check_files(MutualTlsSettings(True))
        with self.assertRaisesRegex(MutualTlsError, "absolute"):
            mtls.check_files(MutualTlsSettings(True, "ca.pem", "c.pem", "k.key"))
        with self.assertRaisesRegex(MutualTlsError, "readable file"):
            mtls.check_files(MutualTlsSettings(True, os.path.abspath("nope-ca"), os.path.abspath("nope-c"), os.path.abspath("nope-k")))

    def test_the_sdk_tls_settings_cannot_be_mixed_in(self):
        mtls.check_no_conflict({})
        for name in mtls.SDK_TLS_NAMES:
            with self.assertRaisesRegex(MutualTlsError, name):
                mtls.check_no_conflict({name: "1"})


class FileChecksTests(unittest.TestCase):
    def files(self, folder, key_mode=0o600):
        for name in ("ca.pem", "client.pem", "client.key"):
            (Path(folder) / name).write_text("x", encoding="utf-8")
        if os.name != "nt":
            os.chmod(Path(folder) / "client.key", key_mode)
        return MutualTlsSettings(True, *(str(Path(folder) / n) for n in ("ca.pem", "client.pem", "client.key")))

    @unittest.skipIf(os.name == "nt", "Windows has no file mode to check")
    def test_a_key_other_users_can_read_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            for mode in (0o640, 0o644, 0o604, 0o660):
                with self.assertRaisesRegex(MutualTlsError, "0600"):
                    mtls.check_files(self.files(folder, mode))
            mtls.check_files(self.files(folder, 0o600))
            mtls.check_files(self.files(folder, 0o400))

    def test_a_key_inside_a_git_working_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".git").mkdir()
            with self.assertRaisesRegex(MutualTlsError, "git working tree"):
                mtls.check_files(self.files(folder))

    def test_unreadable_certificate_content_is_a_clean_error_without_the_content(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self.files(folder)
            with self.assertRaises(MutualTlsError) as caught:
                mtls.build_context(settings)
            self.assertIn("could not be loaded", str(caught.exception))


class FakePaho:
    class CallbackAPIVersion:
        VERSION2 = 2

    made = []

    class _Client:
        def __init__(self, *args, **kwargs):
            self.context = None
            FakePaho.made.append(self)

        def tls_set_context(self, context):
            self.context = context

    Client = _Client


@unittest.skipUnless(OPENSSL, "the openssl command is needed to make test certificates")
class HandshakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.directory.name)
        make_ca(cls.folder, "ca")
        make_ca(cls.folder, "other")
        make_leaf(cls.folder, "ca", "server", True)
        make_leaf(cls.folder, "ca", "client", False)
        make_leaf(cls.folder, "other", "stranger", False)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def serve(self, results):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.folder / "server.pem", self.folder / "server.key")
        context.load_verify_locations(cafile=str(self.folder / "ca.pem"))
        context.verify_mode = ssl.CERT_REQUIRED
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(5)

        def loop():
            while True:
                try:
                    raw, _ = listener.accept()
                except OSError:
                    return
                try:
                    with context.wrap_socket(raw, server_side=True) as tls:
                        results.append(("ok", tls.getpeercert()["subject"]))
                        tls.sendall(b"hello")
                except (ssl.SSLError, OSError) as error:
                    results.append(("refused", type(error).__name__))

        threading.Thread(target=loop, daemon=True).start()
        self.addCleanup(listener.close)
        return listener.getsockname()[1]

    def connect(self, context, port, host="localhost"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return tls.recv(5), tls.version()

    def test_the_bridge_is_accepted_with_its_certificate_and_checks_the_server(self):
        results = []
        port = self.serve(results)
        context = mtls.build_context(settings_in(self.folder))
        data, version = self.connect(context, port)
        self.assertEqual(data, b"hello")
        self.assertIn(version, ("TLSv1.2", "TLSv1.3"))
        self.assertIn((("commonName", "client"),), results[0][1])

    def test_a_certificate_from_another_ca_is_refused_by_the_server(self):
        results = []
        port = self.serve(results)
        context = mtls.build_context(settings_in(self.folder, cert="stranger", key="stranger"))
        with self.assertRaises((ssl.SSLError, OSError)):
            self.connect(context, port)
            # TLS 1.3 reports the refusal on the first read
        deadline = time.time() + 5
        while not results and time.time() < deadline:
            time.sleep(0.05)  # the server thread notes its refusal a moment after the client sees it
        self.assertTrue(results and results[0][0] == "refused")

    def test_a_server_from_another_ca_is_refused_by_the_bridge(self):
        results = []
        port = self.serve(results)
        context = mtls.build_context(settings_in(self.folder, ca="other"))
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.connect(context, port)

    def test_the_server_name_is_checked(self):
        results = []
        port = self.serve(results)
        context = mtls.build_context(settings_in(self.folder))
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.connect(context, port, host="not-the-server.example")

    def test_a_key_that_does_not_belong_to_the_certificate_is_a_clean_error(self):
        with self.assertRaises(MutualTlsError) as caught:
            mtls.build_context(settings_in(self.folder, cert="client", key="stranger"))
        self.assertIn("could not be loaded", str(caught.exception))

    def test_the_context_demands_a_verified_server_and_a_modern_version(self):
        context = mtls.build_context(settings_in(self.folder))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_every_client_made_through_the_module_carries_the_context(self):
        FakePaho.made.clear()
        module = mtls.mqtt_module_for(settings_in(self.folder), env={}, real=FakePaho)
        self.assertEqual(module.CallbackAPIVersion.VERSION2, 2)
        client = module.Client(2, client_id="x")
        self.assertIs(client.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(len(FakePaho.made), 1)

    def test_the_module_is_refused_when_the_sdk_settings_are_also_given(self):
        with self.assertRaisesRegex(MutualTlsError, "HYDRA_MQTT_TLS_CA"):
            mtls.mqtt_module_for(settings_in(self.folder), env={"HYDRA_MQTT_TLS_CA": "/x"}, real=FakePaho)


if __name__ == "__main__":
    unittest.main()
