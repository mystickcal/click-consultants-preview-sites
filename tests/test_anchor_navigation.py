"""Exercise ordinary preview HTML and native anchors without contacting a business.

Run with PREVIEW_FONT_MODE=loaded for the additional remote-font qualification.
The default blocks Google font requests to exercise the required fallback path.
"""
import functools
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted(path for path in (ROOT / "previews").glob("*/index.html")
               if "site.css" in path.read_text())
CONTRACTOR = "global-general-contractors-8f2kq3"
FONT_MODE = os.environ.get("PREVIEW_FONT_MODE", "fallback")
assert FONT_MODE in {"fallback", "loaded"}
WIDTHS = [320, 390, 640, 768, 980, 1024, 1440]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@pytest.fixture(scope="session")
def server():
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(service.server_port)
    service.shutdown()
    thread.join()
    service.server_close()


@pytest.fixture(scope="session", params=["chromium", "firefox", "webkit"])
def browser(request):
    with sync_playwright() as playwright:
        instance = getattr(playwright, request.param).launch()
        yield request.param, instance
        instance.close()


@pytest.fixture
def context(browser):
    _, instance = browser
    context = instance.new_context(viewport={"width": 390, "height": 900},
                                   reduced_motion="reduce")
    forbidden = []

    def route(request_route):
        request = request_route.request
        if request.method not in {"GET", "HEAD"}:
            forbidden.append(request.method + " " + request.url)
            request_route.abort()
        elif FONT_MODE == "fallback" and any(
                host in request.url for host in ["fonts.googleapis.com", "fonts.gstatic.com"]):
            request_route.abort()
        else:
            request_route.continue_()

    context.route("**/*", route)
    yield context
    context.close()
    assert not forbidden


def assets_ready(page):
    page.wait_for_function("""() => Array.from(document.querySelectorAll("img"))
        .every(image => image.complete && image.naturalWidth > 0)""", timeout=15000)
    page.evaluate("() => document.fonts.ready.then(() => true)")
    if FONT_MODE == "loaded":
        assert page.evaluate("""() => Array.from(document.fonts)
            .some(font => font.status === "loaded")"""), "Remote fonts did not load"
    else:
        assert not page.evaluate("""() => Array.from(document.fonts)
            .some(font => font.status === "loaded")""")


def ready(page):
    assets_ready(page)
    page.wait_for_function("""() => {
        const header = document.querySelector(".topbar");
        const position = getComputedStyle(header).position;
        const expected = ["sticky", "fixed"].includes(position)
            ? header.getBoundingClientRect().height : 0;
        return Math.abs(parseFloat(getComputedStyle(document.documentElement)
            .scrollPaddingTop) - expected) < 1;
    }""")


def enlarge_text(page):
    selectors = [".topbar nav a", ".topbar .brand strong",
                 ".topbar .top-cta", "h2", ".eyebrow"]
    sizes = page.evaluate("""selectors => selectors.map(selector => {
        const element = document.querySelector(selector);
        return [selector, element ? parseFloat(getComputedStyle(element).fontSize) : null];
    })""", selectors)
    page.add_style_tag(content="\n".join(
        selector + "{font-size:" + str(size * 1.25) + "px!important}"
        for selector, size in sizes if size is not None))
    ready(page)


def geometry(page, target):
    return page.evaluate("""id => {
        const target = document.getElementById(id);
        const heading = target.matches("h1,h2,h3") ? target : target.querySelector("h1,h2,h3");
        const header = document.querySelector(".topbar");
        const headerRect = header.getBoundingClientRect();
        const position = getComputedStyle(header).position;
        const sticky = ["sticky", "fixed"].includes(position);
        const rect = heading.getBoundingClientRect();
        return {target: id, headerHeight: headerRect.height,
            headerBottom: sticky ? headerRect.bottom : 0,
            headingTop: rect.top, headingBottom: rect.bottom,
            viewport: innerHeight, position};
    }""", target)


def assert_visible(page, target):
    page.wait_for_function("""id => {
        const target = document.getElementById(id);
        if (!target) return false;
        const heading = target.matches("h1,h2,h3") ? target : target.querySelector("h1,h2,h3");
        if (!heading) return false;
        const header = document.querySelector(".topbar");
        const position = getComputedStyle(header).position;
        const bottom = ["sticky", "fixed"].includes(position) ? header.getBoundingClientRect().bottom : 0;
        const rect = heading.getBoundingClientRect();
        return rect.top >= Math.max(0, bottom) - 1 && rect.bottom <= innerHeight + 1;
    }""", arg=target, timeout=3000)
    return geometry(page, target)


def test_all_shared_styles_reveal_native_section_anchors(context, browser, server):
    engine, _ = browser
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cases = []
    assert len(PAGES) == 10
    for source in PAGES:
        widths = WIDTHS if engine == "chromium" or source.parent.name == CONTRACTOR \
            else [320, 768, 1440]
        route = "/" + source.parent.relative_to(ROOT).as_posix() + "/"
        for width in widths:
            for scale in [1, 1.25]:
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(server + route, wait_until="networkidle")
                ready(page)
                if scale > 1:
                    enlarge_text(page)
                targets = page.locator('.topbar nav a[href^="#"]').all()
                assert targets
                measurements = []
                for index, link in enumerate(targets):
                    target = link.get_attribute("href")[1:]
                    assert page.locator('[id="' + target + '"]').count() == 1
                    if index % 2:
                        link.focus()
                        link.press("Enter")
                    else:
                        link.click()
                    measurements.append(assert_visible(page, target))
                    assert page.url.endswith("#" + target)
                cases.append({"page": route, "width": width, "textScale": scale,
                              "anchors": measurements})
    assert not errors
    destination = ROOT / ".audit-state" / ("anchor-" + engine + "-" + FONT_MODE + ".json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps({"engine": engine, "fontMode": FONT_MODE,
                                      "cases": cases, "pageErrors": errors}, indent=2))
    page.close()


def test_direct_hash_history_resize_and_reduced_motion(context, browser, server):
    engine, _ = browser
    page = context.new_page()
    route = "/previews/" + CONTRACTOR + "/"
    page.goto(server + route + "#services", wait_until="networkidle")
    ready(page)
    assert_visible(page, "services")
    assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
    link = page.locator('.topbar nav a[href="#projects"]')
    link.focus()
    link.press("Enter")
    assert_visible(page, "projects")
    page.go_back()
    assert page.url.endswith("#services")
    assert_visible(page, "services")
    page.go_forward()
    assert page.url.endswith("#projects")
    assert_visible(page, "projects")
    for width in [1024, 768, 390, 320]:
        page.set_viewport_size({"width": width, "height": 900})
        ready(page)
        page.locator('.topbar nav a[href="#services"]').click()
        assert_visible(page, "services")
    enlarge_text(page)
    page.locator('.topbar nav a[href="#areas"]').press("Enter")
    assert_visible(page, "areas")
    page.screenshot(path=str(ROOT / ".audit-state" / ("anchor-phone-" + engine + "-" + FONT_MODE + ".png")))
    page.close()


def test_default_motion_native_anchor_finishes_below_header(context, server):
    page = context.new_page()
    page.emulate_media(reduced_motion="no-preference")
    page.goto(server + "/previews/" + CONTRACTOR + "/", wait_until="networkidle")
    ready(page)
    assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "smooth"
    for target in ["services", "projects", "areas"]:
        page.locator('.topbar nav a[href="#' + target + '"]').press("Enter")
        settle_scroll(page)
        assert_visible(page, target)
    page.close()

def settle_scroll(page):
    page.evaluate("""async () => {
        let previous = scrollY, stable = 0;
        for (let frame = 0; frame < 180; frame++) {
            await new Promise(requestAnimationFrame);
            stable = scrollY === previous ? stable + 1 : 0;
            previous = scrollY;
            if (stable >= 8) return;
        }
        throw new Error("Native anchor scrolling did not settle");
    }""")


@pytest.fixture
def upgrade_server():
    """Serve two bounded actual old files, then the published candidate at one origin."""
    previous = "6f88ea6678a592a0f2adac249a79184ee3269396"
    paths = ["site.css", "previews/" + CONTRACTOR + "/index.html"]
    baseline = {}
    for path in paths:
        size = int(subprocess.check_output(
            ["git", "cat-file", "-s", previous + ":" + path], cwd=ROOT, text=True))
        assert size <= 65536
        baseline["/" + path] = subprocess.check_output(
            ["git", "show", previous + ":" + path], cwd=ROOT)
    assert sum(map(len, baseline.values())) <= 131072
    upgraded = threading.Event()

    class UpgradeHandler(QuietHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            lookup = self.path.split("?")[0]
            if lookup.endswith("/"):
                lookup += "index.html"
            if not upgraded.is_set() and lookup in baseline:
                source = baseline[lookup]
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(lookup))
                self.send_header("Content-Length", str(len(source)))
                self.end_headers()
                self.wfile.write(source)
                return
            super().do_GET()

    handler = functools.partial(UpgradeHandler, directory=str(ROOT))
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(service.server_port), upgraded.set
    service.shutdown()
    thread.join()
    service.server_close()


def test_history_from_previous_release_reveals_restored_anchor(context, browser, upgrade_server):
    engine, _ = browser
    origin, upgrade = upgrade_server
    page = context.new_page()
    page.emulate_media(reduced_motion="no-preference")
    page.goto(origin + "/previews/" + CONTRACTOR + "/", wait_until="networkidle")
    assets_ready(page)
    page.locator('.topbar nav a[href="#services"]').press("Enter")
    settle_scroll(page)
    before = geometry(page, "services")
    assert before["headingTop"] < before["headerBottom"] - 1
    upgrade()
    page.reload(wait_until="networkidle")
    ready(page)
    assert_visible(page, "services")
    page.locator('.topbar nav a[href="#projects"]').press("Enter")
    settle_scroll(page)
    assert_visible(page, "projects")
    page.go_back()
    settle_scroll(page)
    assert page.url.endswith("#services")
    restored = assert_visible(page, "services")
    page.go_forward()
    settle_scroll(page)
    assert page.url.endswith("#projects")
    assert_visible(page, "projects")
    receipt = ROOT / ".audit-state" / ("history-upgrade-" + engine + "-" + FONT_MODE + ".json")
    receipt.write_text(json.dumps({"engine": engine, "fontMode": FONT_MODE,
                                  "oldCommit": "6f88ea6678a592a0f2adac249a79184ee3269396",
                                  "oldGeometry": before, "restoredGeometry": restored}, indent=2))
    page.close()
