import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
import phone_agent


class _Probe(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/generate_204":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/portal":
            body = b"<html>Login</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/portal")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class HttpCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Probe)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.old_url = phone_agent.LAT_URL

    @classmethod
    def tearDownClass(cls):
        phone_agent.LAT_URL = cls.old_url
        cls.server.shutdown()

    def check(self, path):
        phone_agent.LAT_URL = self.base + path
        return phone_agent.http_check(timeout_s=3)

    def test_204_is_internet(self):
        r = self.check("/generate_204")
        self.assertTrue(r["http_ok"])
        self.assertEqual(r["http_code"], 204)

    def test_portal_page_is_not_internet(self):
        r = self.check("/portal")
        self.assertFalse(r["http_ok"])
        self.assertEqual(r["http_code"], 200)

    def test_redirect_is_reported_not_followed(self):
        r = self.check("/redirect")
        self.assertFalse(r["http_ok"])
        self.assertEqual(r["http_code"], 302)

    def test_server_error_is_not_internet(self):
        r = self.check("/missing")
        self.assertFalse(r["http_ok"])
        self.assertEqual(r["http_code"], 404)


if __name__ == "__main__":
    unittest.main()
