"""Screenshots for the Mac install video (run on a CI macOS desktop).

    python desktop/macos/capture_install_video.py <app.dmg> <out_dir>

Walks through a real install: Downloads, the disk image window, Applications,
the first-open security check, then the welcome page in Chrome (archive, keys,
step 4), the menu-bar icon and reopening from Applications. Saves full-screen
PNGs plus meta.json with rectangles (in screenshot pixels) to highlight.
Calls to Anthropic/Voyage are mocked with the app's real wording; the keys
typed are dummies. Each step is best-effort: a failure is logged, the rest runs.
"""
import faulthandler
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import Quartz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

DMG = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
META = {"shots": {}, "log": []}
APP_NAME = "Provenance Wiki (Testing)"
APP = Path("/Applications") / f"{APP_NAME}.app"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SCALE = 1.0   # screenshot pixels per screen point, set at start


def log(msg):
    print(msg, flush=True)
    META["log"].append(msg)
    (OUT / "meta.json").write_text(json.dumps(META, indent=1), encoding="utf-8")


def sh(*cmd, **kw):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)


def grab(path):
    sh("screencapture", "-x", "-C", str(path))
    return Image.open(path)


def shot(sid, boxes=None, note=None):
    """boxes are in screen points; stored in screenshot pixels."""
    img = grab(OUT / f"{sid}.png")
    META["shots"][sid] = {"file": f"{sid}.png", "size": img.size,
                          "boxes": {k: [round(v * SCALE) for v in b] for k, b in (boxes or {}).items() if b},
                          "note": note}
    log(f"shot {sid} boxes={[k for k, b in (boxes or {}).items() if b]}")


def step(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            log(f"STEP FAILED {fn.__name__}:\n{traceback.format_exc()}")
            try:
                grab(OUT / f"error-{fn.__name__}.png")
            except Exception:
                pass
    run.__name__ = fn.__name__
    return run


# ── Windows, clicks, keys (Quartz) ───────────────────────────────────────────

def windows():
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []:
        b = w.get("kCGWindowBounds", {})
        out.append({"owner": w.get("kCGWindowOwnerName", ""), "name": w.get("kCGWindowName", "") or "",
                    "layer": w.get("kCGWindowLayer", 0),
                    "box": [b.get("X", 0), b.get("Y", 0), b.get("X", 0) + b.get("Width", 0),
                            b.get("Y", 0) + b.get("Height", 0)]})
    return out


def window_summary():
    return "; ".join(f"{w['owner']}/{w['name']!r}@{w['layer']}{[int(v) for v in w['box']]}" for w in windows())


def find_win(pred, timeout=10, largest=True):
    end = time.time() + timeout
    while time.time() < end:
        hits = [w for w in windows() if pred(w)]
        if hits:
            hits.sort(key=lambda w: (w["box"][2] - w["box"][0]) * (w["box"][3] - w["box"][1]), reverse=largest)
            return hits[0]
        time.sleep(0.5)
    return None


def click(x, y):
    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        e = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.08)


def scroll(x, y, lines):
    move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (x, y), Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    for _ in range(abs(lines)):
        e = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, -1 if lines > 0 else 1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.03)


def key(code):
    for down in (True, False):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, code, down))
        time.sleep(0.05)


ESC = 53


def close_finder_windows():
    sh("killall", "Finder")
    time.sleep(3)


# ── Sample photographs ───────────────────────────────────────────────────────

FONT_DIRS = [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"), Path("/Library/Fonts")]


def font(names, size):
    for n in names:
        for d in FONT_DIRS:
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    return ImageFont.load_default()


def make_document_photos(folder):
    """Invented sample documents photographed on a desk (no real records)."""
    docs = [
        ("dealer-letter-1931-03-12.jpg", "GALERIE EXEMPLE", "12 rue Imaginaire, Paris",
         ["Paris, 12 March 1931", "", "Dear Sir,", "",
          "Further to your visit, I confirm the sale of", "the landscape by J. Example (oil on canvas,",
          "54 x 73 cm) for the sum of 18,000 francs.", "",
          "The painting will be delivered to your", "residence on the 20th of this month.", "",
          "With my respectful regards,", "", "A. Exemple"]),
        ("invoice-214.jpg", "INVOICE No. 214", "Example & Co., Fine Art Dealers, London",
         ["Date: 4 June 1928", "", "Sold to: Mr. B. Sample", "",
          "1. Still life with flowers, oil      £ 240", "2. Portrait study, drawing          £  35",
          "3. Gilt frame                        £  12", "", "Total                               £ 287",
          "", "Received with thanks."]),
        ("exhibition-label.jpg", "EXHIBITION OF MODERN PAINTING", "Example Gallery, 1937",
         ["No. 42", "", "Harbour at Dawn", "Oil on canvas", "", "Lent by a private collector"]),
    ]
    rnd = random.Random(7)
    head = font(["Georgia Bold.ttf", "Times New Roman Bold.ttf"], 64)
    sub = font(["Georgia Italic.ttf", "Times New Roman Italic.ttf"], 38)
    body = font(["Courier New.ttf", "Courier.ttc"], 40)
    for name, title, subtitle, lines in docs:
        paper = Image.new("RGB", (1500, 2000), (244, 236, 214))
        d = ImageDraw.Draw(paper)
        for _ in range(9000):
            x, y = rnd.randrange(1500), rnd.randrange(2000)
            g = rnd.randrange(215, 240)
            d.point((x, y), fill=(g + 10, g + 3, g - 15))
        d.text((750, 170), title, font=head, fill=(40, 34, 30), anchor="mm")
        d.text((750, 250), subtitle, font=sub, fill=(70, 60, 52), anchor="mm")
        d.line((180, 310, 1320, 310), fill=(90, 80, 70), width=3)
        y = 420
        for ln in lines:
            d.text((190, y), ln, font=body, fill=(35, 32, 40))
            y += 70
        paper = paper.filter(ImageFilter.GaussianBlur(0.6)).rotate(rnd.uniform(-2.5, 2.5), expand=True,
                                                                   fillcolor=(0, 0, 0))
        desk = Image.new("RGB", (1900, 2500), (96, 70, 48))
        dd = ImageDraw.Draw(desk)
        for i in range(0, 2500, 9):
            c = 88 + int(10 * ((i * 7919) % 13) / 13)
            dd.line((0, i, 1900, i + rnd.randint(-6, 6)), fill=(c, c - 22, c - 42), width=5)
        mask = paper.convert("L").point(lambda v: 255 if v > 8 else 0)
        desk.paste(paper, ((1900 - paper.size[0]) // 2, (2500 - paper.size[1]) // 2), mask)
        desk.save(folder / name, quality=88)


# ── 1. Downloads, disk image, Applications, first-open check ─────────────────

def finder_window():
    return find_win(lambda w: w["owner"] == "Finder" and w["layer"] == 0 and w["box"][3] - w["box"][1] > 150, 10)


@step
def install():
    dl = Path.home() / "Downloads"
    dl.mkdir(exist_ok=True)
    target = dl / DMG.name
    shutil.copy2(DMG, target)
    # Mark it as downloaded by a browser, like a real download
    sh("xattr", "-w", "com.apple.quarantine", f"0083;{int(time.time()):x};Safari;", str(target))
    close_finder_windows()
    sh("open", "-R", str(target))
    time.sleep(4)
    fw = finder_window()
    shot("downloads", {"window": fw["box"] if fw else None})

    sh("open", str(target))           # mounts the disk image and opens its window
    vol = Path("/Volumes") / APP_NAME
    for _ in range(60):
        if (vol / f"{APP_NAME}.app").exists():
            break
        time.sleep(0.5)
    time.sleep(4)
    fw = find_win(lambda w: w["owner"] == "Finder" and w["layer"] == 0 and APP_NAME in w["name"], 10) or finder_window()
    shot("dmg", {"window": fw["box"] if fw else None})
    log("after dmg: " + window_summary())

    # The drag onto Applications, done the way Finder copies (keeps the quarantine mark)
    sh("ditto", str(vol / f"{APP_NAME}.app"), str(APP))
    close_finder_windows()
    sh("open", "-R", str(APP))
    time.sleep(4)
    fw = finder_window()
    shot("applications", {"window": fw["box"] if fw else None})

    # First open: macOS checks the app (`open` waits while the dialog is up)
    subprocess.Popen(["open", str(APP)])
    time.sleep(8)
    log("after first open: " + window_summary())
    # Wait past the "Verifying…" bar for the real dialog (much taller)
    dlg = find_win(lambda w: w["owner"] == "CoreServicesUIAgent" and w["box"][3] - w["box"][1] > 150, 40)
    time.sleep(1)
    shot("gatekeeper", {"dialog": dlg["box"] if dlg else None})
    if dlg:
        b = dlg["box"]
        w, h = b[2] - b[0], b[3] - b[1]
        click(b[0] + 0.27 * w, b[1] + 0.885 * h)          # "Done"
        time.sleep(2)
        still = find_win(lambda w: w["owner"] == "CoreServicesUIAgent" and w["box"][3] - w["box"][1] > 150, 1)
        log(f"clicked Done: dialog {'still open' if still else 'closed'}")
        if still:
            sh("pkill", "-x", "CoreServicesUIAgent")
    close_finder_windows()

    sh("open", "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension")
    time.sleep(7)
    sw = find_win(lambda w: w["owner"] == "System Settings" and w["layer"] == 0, 10)
    shot("settings", {"window": sw["box"] if sw else None})
    if sw:
        b = sw["box"]
        for _ in range(12):
            scroll(b[0] + (b[2] - b[0]) * 0.65, (b[1] + b[3]) / 2, 30)
            time.sleep(0.6)
        time.sleep(1.5)
        shot("settings_scrolled", {"window": sw["box"]})
    sh("pkill", "-x", "System Settings")

    # Let the app open for the rest of the video (the Open Anyway approval can't be
    # given on CI: it needs the Mac password), and keep it from opening a browser.
    sh("xattr", "-dr", "com.apple.quarantine", str(APP))
    # The first-open dialog can't be clicked on CI (no accessibility access):
    # stop the waiting `open` and the dialog, then start the app directly.
    sh("pkill", "-f", f"open {APP}")
    for _ in range(5):
        sh("pkill", "-9", "-x", "CoreServicesUIAgent")
        time.sleep(1.5)
        if not find_win(lambda w: w["owner"] == "CoreServicesUIAgent", 1):
            break
    log("before app start: " + window_summary())
    env = dict(os.environ, PW_NO_BROWSER="1")
    subprocess.Popen([str(APP / "Contents" / "MacOS" / "ProvenanceWiki")], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ── 2. The app in Chrome ─────────────────────────────────────────────────────

def chrome_front():
    sh("open", "-a", "Google Chrome")
    time.sleep(1.2)


def box_of(page, locator):
    b = locator.bounding_box(timeout=8000)
    if not b:
        return None
    m = page.evaluate("({x: window.screenX, y: window.screenY + window.outerHeight - window.innerHeight,"
                      " z: window.devicePixelRatio})")
    return [m["x"] + b["x"], m["y"] + b["y"], m["x"] + b["x"] + b["width"], m["y"] + b["y"] + b["height"]]


def to_center(page, locator):
    locator.evaluate("e => e.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(700)


def section(page, heading_text):
    return page.locator("section", has=page.locator("h2", has_text=heading_text)).first


def app_flow():
    from playwright.sync_api import sync_playwright
    cfg = Path.home() / "Library" / "Application Support" / "ProvenanceWiki Config" / "instance.json"
    for _ in range(120):
        if cfg.exists():
            break
        time.sleep(0.5)
    port = json.loads(cfg.read_text())["port"]
    url = f"http://127.0.0.1:{port}/"
    log(f"app at {url}")
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    sw, shh = int(b.size.width), int(b.size.height)
    subprocess.Popen([CHROME, "--user-data-dir=/tmp/chrome-video", "--no-first-run", "--no-default-browser-check",
                      "--remote-debugging-port=9222", "--window-position=0,0", f"--window-size={sw},{shh}",
                      "--disable-features=TranslateUI", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(7)
    chrome_front()
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        page.wait_for_url("**/settings?welcome=1", timeout=30000)
        page.wait_for_selector("text=Create my archive here", timeout=30000)
        page.wait_for_timeout(1500)
        for fn in (welcome, archive, keys, step3, step4):
            step(fn)(page)
        browser.close()


def welcome(page):
    s1 = section(page, "Where to store your archive")
    shot("welcome", {"heading": box_of(page, page.locator("h1")), "step1": box_of(page, s1)})
    btn = page.get_by_role("button", name="Create my archive here")
    shot("archive_create", {"button": box_of(page, btn), "step1": box_of(page, s1)})


def archive(page):
    other = page.get_by_text("Choose another folder", exact=True).first
    other_box = box_of(page, other.locator("xpath=ancestor::label[1]"))
    other.click()
    dlg = find_win(lambda w: w["owner"] == "osascript" and w["layer"] == 0, 15)
    time.sleep(1.5)
    log("picker: " + window_summary())
    shot("archive_other", {"button": other_box, "dialog": dlg["box"] if dlg else None})
    chrome_front()
    cancel = page.get_by_role("button", name="Cancel folder window")
    if cancel.count():
        cancel.first.click()
    else:
        sh("pkill", "-x", "osascript")
    page.wait_for_timeout(1500)
    page.get_by_text("Use the suggested location", exact=True).first.click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="Create my archive here").click()
    page.wait_for_selector("text=Where your archive is stored", timeout=30000)
    page.wait_for_timeout(1200)
    s = section(page, "Where your archive is stored")
    to_center(page, s)
    shot("archive_done", {"section": box_of(page, s)})


TEST_MSG = {"anthropic": "Anthropic accepted the key. (This check is free; it cannot confirm account credit.)",
            "voyage": "Voyage AI accepted the key."}


def keys(page):
    page.route("**/api/settings/keys/*/test",
               lambda r: r.fulfill(json={"status": "ok", "message": TEST_MSG["anthropic" if "anthropic" in r.request.url else "voyage"]}))
    s = section(page, "Your API keys")
    a_in = page.get_by_label("Anthropic", exact=False).first
    v_in = page.get_by_label("Voyage", exact=False).first
    to_center(page, a_in)
    a_in.fill("sk-ant-api03-DEMO-not-a-real-key-0000000000")
    page.wait_for_timeout(400)
    a_row = a_in.locator("xpath=..")
    shot("keys_paste", {"input": box_of(page, a_in), "save": box_of(page, a_row.get_by_role("button", name="Save")),
                        "section": box_of(page, s)})
    a_row.get_by_role("button", name="Save").click()
    page.wait_for_timeout(2500)
    log("after anthropic save: " + window_summary())
    v_in.fill("pa-DEMO-not-a-real-key-000000000000")
    v_in.locator("xpath=..").get_by_role("button", name="Save").click()
    page.wait_for_timeout(2500)
    page.get_by_label("Anthropic", exact=False).first.locator("xpath=..").get_by_role("button", name="Test connection").click()
    page.wait_for_timeout(800)
    page.get_by_label("Voyage", exact=False).first.locator("xpath=..").get_by_role("button", name="Test connection").click()
    page.wait_for_timeout(1200)
    to_center(page, page.get_by_text("Anthropic accepted the key", exact=False))
    tests = page.get_by_role("button", name="Test connection")
    shot("keys_test", {"test1": box_of(page, tests.nth(0)), "test2": box_of(page, tests.nth(1)),
                       "ok1": box_of(page, page.get_by_text("Anthropic accepted the key", exact=False)),
                       "ok2": box_of(page, page.get_by_text("Voyage AI accepted the key", exact=False))})
    page.unroute("**/api/settings/keys/*/test")


def step3(page):
    s = section(page, "What leaves this computer")
    to_center(page, s)
    shot("step3", {"section": box_of(page, s)})


def find_key(obj, k):
    if isinstance(obj, dict):
        for kk, v in obj.items():
            if kk == k and isinstance(v, str):
                return v
            r = find_key(v, k)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, k)
            if r:
                return r
    return None


def step4(page):
    s = page.locator("#add-photos")
    open_btn = s.get_by_role("button", name="Open photos folder")
    to_center(page, open_btn)
    shot("step4_open", {"button": box_of(page, open_btn), "heading": box_of(page, s.locator("h2"))})
    settings = page.evaluate("() => fetch('/api/settings').then(r => r.json())")
    photos = Path(find_key(settings, "photos"))
    log(f"photos folder: {photos}")
    make_document_photos(photos)
    open_btn.click()
    time.sleep(5)
    fw = finder_window()
    shot("photos_folder", {"window": fw["box"] if fw else None})
    close_finder_windows()
    chrome_front()
    check = s.get_by_role("button", name="I’ve added photos: check again")
    to_center(page, check)
    check.click()
    page.wait_for_selector("text=new photographs ready to ingest", timeout=15000)
    page.wait_for_timeout(600)
    shot("check_again", {"link": box_of(page, check),
                         "status": box_of(page, s.get_by_text("ready to ingest", exact=False))})
    src = s.get_by_label("Source")
    to_center(page, src)
    src.click()
    src.type("Test archive: dealer letters", delay=40)
    page.wait_for_timeout(500)
    shot("source", {"input": box_of(page, src),
                    "label": box_of(page, s.get_by_text("Say where these documents", exact=False))})
    sse = "".join(f"data: {json.dumps(e)}\n\n" for e in [
        {"type": "scan", "total": 3, "message": "Found 3 image files"},
        {"type": "done_file", "completed": 1, "total": 3, "file": "dealer-letter-1931-03-12.jpg"},
        {"type": "done_file", "completed": 2, "total": 3, "file": "exhibition-label.jpg"},
        {"type": "done_file", "completed": 3, "total": 3, "file": "invoice-214.jpg"},
        {"type": "done", "processed": 3}])
    page.route("**/api/ingest", lambda r: r.fulfill(status=202, json={"status": "started", "message": "Ingestion started"}))
    page.route("**/api/ingest/progress", lambda r: r.fulfill(status=200, body=sse, headers={"Content-Type": "text/event-stream"}))
    ing = s.get_by_role("button", name="⊕ Ingest Photos")
    to_center(page, ing)
    ing.click()
    page.wait_for_selector("text=Done: 3 processed", timeout=15000)
    page.wait_for_timeout(800)
    shot("ingest", {"button": box_of(page, ing),
                    "result": box_of(page, s.get_by_text("Done: 3 processed", exact=False))})


# ── 3. Menu-bar icon, reopening ──────────────────────────────────────────────

@step
def menubar():
    ours = lambda w: "Provenance" in w["owner"] or "ProvenanceWiki" in w["owner"]
    item = find_win(lambda w: ours(w) and w["layer"] >= 20 and w["box"][1] < 40, 10, largest=False)
    log("menu bar: " + window_summary())
    if not item:
        shot("menubar")
        return
    shot("menubar", {"icon": item["box"]})
    x, y = (item["box"][0] + item["box"][2]) / 2, (item["box"][1] + item["box"][3]) / 2
    click(x, y)
    time.sleep(1.5)
    menu = find_win(lambda w: ours(w) and w["layer"] >= 20 and w["box"][3] - w["box"][1] > 40, 5)
    log("menu: " + window_summary())
    shot("menu", {"menu": menu["box"] if menu else None, "icon": item["box"]})
    key(ESC)
    time.sleep(0.5)


@step
def reopen():
    close_finder_windows()
    sh("open", "-R", str(APP))
    time.sleep(4)
    fw = finder_window()
    shot("reopen", {"window": fw["box"] if fw else None})


@step
def set_resolution():
    """Use the largest unscaled mode up to 1920 wide (CI Macs start at 1024x768)."""
    out = sh("displayplacer", "list").stdout
    sid = re.search(r"Persistent screen id: (\S+)", out)
    best = None
    for m in re.finditer(r"mode (\d+): res:(\d+)x(\d+) hz:(\d+)[^\n]*", out):
        n, w, h = int(m.group(1)), int(m.group(2)), int(m.group(3))
        line = m.group(0)
        if w <= 1920 and "scaling:on" not in line and (best is None or w * h > best[1] * best[2]):
            best = (n, w, h)
    log(f"displayplacer modes: best={best}")
    if sid and best:
        r = sh("displayplacer", f"id:{sid.group(1)} mode:{best[0]}")
        log(f"displayplacer -> {r.returncode} {r.stdout.strip()} {r.stderr.strip()}")
        time.sleep(3)


if __name__ == "__main__":
    faulthandler.dump_traceback_later(14 * 60, exit=True)
    set_resolution()
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    SCALE = grab(OUT / "_probe.png").size[0] / b.size.width
    log(f"screen {b.size.width}x{b.size.height} points, scale {SCALE}")
    install()
    step(app_flow)()
    menubar()
    reopen()
    log("windows at end: " + window_summary())
    log("done")
