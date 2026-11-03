"""The reader has no model-configuration UI or outgoing settings API calls."""
import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from tests.test_frontend_v3 import StaticHandler


class ModelSettingsFrontendTests(unittest.TestCase):
    def test_assets_have_no_model_configuration_entry_or_api(self):
        for name in ("reader-v3.html", "reader-v3.js", "styles-v3.css"):
            with self.subTest(asset=name):
                source = (SCRIPTS.parent / "assets" / name).read_text(encoding="utf-8")
                self.assertNotIn("model-settings", source)
                self.assertNotIn("modelSettings", source)

    def test_reader_boots_and_navigates_without_model_configuration_requests(self):
        from playwright.sync_api import sync_playwright
        server = ThreadingHTTPServer(("127.0.0.1", 0), StaticHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        requests, errors = [], []
        def route_api(route):
            request = route.request
            path = request.url.split("/api/", 1)[1].split("?")[0]
            requests.append((request.method, path))
            data = {}
            if path == "session":
                data = {"token": "test-token", "api_version": 3}
            elif path == "status":
                data = {"bridge_available": False, "bridges": [], "counts": {}}
            elif path.endswith("/annotations"):
                data = {"annotations": [], "revision": 0}
            elif path == "events":
                route.fulfill(status=200, content_type="text/event-stream", body=": test\n\n")
                return
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/**", route_api)
                page.goto("http://127.0.0.1:" + str(server.server_address[1]) + "/reader-v3.html")
                page.wait_for_function("Boolean(window.ReaderV3)")
                self.assertEqual(0, page.locator("[id^='model-settings'], .model-settings").count())
                self.assertEqual(0, page.get_by_role("button", name="模型设置", exact=True).count())
                page.locator(".view-tabs [data-view='explanation']").click()
                page.locator(".view-tabs [data-view='overview']").click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#toc-toggle").click()
                self.assertEqual(0, page.locator("[id^='model-settings'], .model-settings").count())
                self.assertEqual([], errors)
                self.assertFalse(any(path == "model-settings" for _, path in requests), requests)
                self.assertTrue(all(method == "GET" for method, _ in requests), requests)
                browser.close()
        finally:
            server.shutdown();worker.join(2);server.server_close()


if __name__ == "__main__":
    unittest.main()
