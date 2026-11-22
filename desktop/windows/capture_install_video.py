"""Screenshots for the Windows install video (run on a CI Windows desktop).

    python desktop/windows/capture_install_video.py <Setup.exe> <out_dir>

Walks through a real install the way a researcher would: Downloads folder, the
installer wizard, the welcome page in Edge (archive, keys, step 4), the tray
icon and the Start menu. Saves full-screen PNGs plus meta.json with the screen
rectangles of the things to highlight. Network calls to Anthropic/Voyage are
replaced by mocked responses with the app's real wording; the API keys typed
are dummies. Every step is best-effort: a failure is logged and the rest runs.
"""
import faulthandler
import json
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import win32api
import win32con
import win32gui
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageGrab
from pywinauto import Desktop, keyboard

SETUP = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
META = {"shots": {}, "log": []}
UIA = Desktop(backend="uia")


def log(msg):
    print(msg, flush=True)
    META["log"].append(msg)
    (OUT / "meta.json").write_text(json.dumps(META, indent=1), encoding="utf-8")


def grab():
    return ImageGrab.grab(all_screens=True)


def shot(sid, boxes=None, note=None):
    img = grab()
    img.save(OUT / f"{sid}.png")
    META["shots"][sid] = {"file": f"{sid}.png", "size": img.size,
                          "boxes": {k: [int(v) for v in b] for k, b in (boxes or {}).items() if b},
                          "note": note}
    log(f"shot {sid} boxes={list((boxes or {}).keys())}")


def rect(ctrl):
    try:
        r = ctrl.rectangle()
        return [r.left, r.top, r.right, r.bottom]
    except Exception:
        return None


def step(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            log(f"STEP FAILED {fn.__name__}:\n{traceback.format_exc()}")
            try:
                grab().save(OUT / f"error-{fn.__name__}.png")
            except Exception:
                pass
    run.__name__ = fn.__name__
    return run


def window_list():
    out = []
    for w in UIA.windows():
        try:
            out.append(f"{w.window_text()!r} [{w.class_name()}]")
        except Exception:
            pass
    return out


def find_window(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for w in UIA.windows():
            try:
                if pred(w.window_text(), w.class_name()):
                    return w
            except Exception:
                pass
        time.sleep(0.5)
    return None


def find_desc(win, pred, control_type=None):
    for c in win.descendants(control_type=control_type) if control_type else win.descendants():
        try:
            if pred(c.window_text()):
                return c
        except Exception:
            pass
    return None


# ── Setup: screen size, sample photographs ────────────────────────────────────

@step
def tidy_desktop():
    """Close or minimise runner windows that would show up in the screenshots."""
    for w in UIA.windows():
        try:
            t, c = w.window_text(), w.class_name()
            if t == "System Properties":
                w.close()
            elif c in ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"):
                win32gui.ShowWindow(w.handle, win32con.SW_MINIMIZE)
        except Exception:
            pass
    time.sleep(1)


@step
def set_resolution():
    dm = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
    dm.PelsWidth, dm.PelsHeight = 1920, 1080
    dm.Fields = win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT
    r = win32api.ChangeDisplaySettings(dm, 0)
    time.sleep(2)
    log(f"ChangeDisplaySettings -> {r}; screen now {grab().size}")


def font(names, size):
    for n in names:
        p = Path(r"C:\Windows\Fonts") / n
        if p.exists():
            return ImageFont.truetype(str(p), size)
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
    head = font(["georgiab.ttf", "timesbd.ttf", "arialbd.ttf"], 64)
    sub = font(["georgiai.ttf", "timesi.ttf", "arial.ttf"], 38)
    body = font(["cour.ttf", "consola.ttf", "arial.ttf"], 40)
    for name, title, subtitle, lines in docs:
        paper = Image.new("RGB", (1500, 2000), (244, 236, 214))
        d = ImageDraw.Draw(paper)
        for _ in range(9000):  # paper grain
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
        for i in range(0, 2500, 9):  # wood grain
            c = 88 + int(10 * ((i * 7919) % 13) / 13)
            dd.line((0, i, 1900, i + rnd.randint(-6, 6)), fill=(c, c - 22, c - 42), width=5)
        mask = Image.new("L", paper.size, 0)
        ImageDraw.Draw(mask).rectangle((4, 4, paper.size[0] - 5, paper.size[1] - 5), fill=255)
        mask = paper.convert("L").point(lambda v: 255 if v > 8 else 0)
        desk.paste(paper, ((1900 - paper.size[0]) // 2, (2500 - paper.size[1]) // 2), mask)
        desk.save(folder / name, quality=88)


# ── 1. Downloads folder, SmartScreen, installer wizard ───────────────────────

@step
def downloads_and_launch():
    dl = Path.home() / "Downloads"
    dl.mkdir(exist_ok=True)
    target = dl / SETUP.name
    shutil.copy2(SETUP, target)
    keyboard.send_keys("{VK_LWIN down}d{VK_LWIN up}")
    time.sleep(1.5)
    subprocess.Popen(["explorer.exe", "/select,", str(target)])
    ex = find_window(lambda t, c: c == "CabinetWClass", 20)
    time.sleep(3)
    if ex:
        win32gui.MoveWindow(ex.handle, 180, 110, 1500, 820, True)
        ex.set_focus()
        time.sleep(1.5)
        item = find_desc(ex, lambda t: t.startswith("ProvenanceWiki-Setup"), "ListItem")
        shot("downloads", {"file": rect(item), "window": rect(ex)})
    else:
        shot("downloads")
    # (SmartScreen can't be reproduced on CI: the video shows an illustration.)
    subprocess.Popen([str(target)])
    time.sleep(10)
    log("windows after launch: " + "; ".join(window_list()))
    shot("after_launch")

    ss = find_window(lambda t, c: "SmartScreen" in t or "protected your PC" in t, 5)
    if ss:
        more = find_desc(ss, lambda t: t.strip().lower() == "more info")
        shot("smartscreen1", {"more_info": rect(more), "window": rect(ss)})
        if more:
            more.click_input()
            time.sleep(2)
        run = find_desc(ss, lambda t: t.strip().lower() == "run anyway", "Button")
        shot("smartscreen2", {"run_anyway": rect(run), "window": rect(ss)})
        if run:
            run.click_input()
    sec = find_window(lambda t, c: "Security Warning" in t, 2)
    if sec:
        btn = find_desc(sec, lambda t: t.strip() == "Run", "Button")
        shot("security_warning", {"run": rect(btn), "window": rect(sec)})
        if btn:
            btn.click_input()
    if ex:
        try:
            ex.close()
        except Exception:
            pass


def region_changed(a, b, box):
    return ImageChops.difference(a.crop(box), b.crop(box)).getbbox() is not None


@step
def wizard():
    wiz = find_window(lambda t, c: t.startswith("Setup - "), 90)
    if not wiz:
        raise RuntimeError("installer window not found: " + "; ".join(window_list()))
    wiz.set_focus()
    done = set()
    for _ in range(25):
        time.sleep(1.5)
        try:
            texts = " | ".join(c.window_text() for c in wiz.descendants() if c.window_text())
        except Exception:
            break  # window closed
        nxt = find_desc(wiz, lambda t: t.startswith("Next"), "Button")
        win = rect(wiz)
        if "License Agreement" in texts and "license" not in done:
            acc = find_desc(wiz, lambda t: t.startswith("I accept"), "RadioButton")
            shot("license", {"accept": rect(acc), "next": rect(nxt), "window": win})
            acc.click_input()
            time.sleep(0.6)
            shot("license_accepted", {"accept": rect(acc), "next": rect(nxt), "window": win})
            nxt.click_input(); done.add("license")
        elif "Information" in texts and "infobefore" not in done:
            shot("infobefore", {"next": rect(nxt), "window": win})
            nxt.click_input(); done.add("infobefore")
        elif "Select Destination Location" in texts and "selectdir" not in done:
            shot("selectdir", {"next": rect(nxt), "window": win})
            nxt.click_input(); done.add("selectdir")
        elif "Select Additional Tasks" in texts and "tasks" not in done:
            box = find_desc(wiz, lambda t: "desktop shortcut" in t.lower())
            lst = None
            for c in wiz.descendants():
                if "CheckListBox" in (c.class_name() or ""):
                    lst = c
                    break
            target = rect(box) or rect(lst)
            shot("tasks", {"checkbox": target, "next": rect(nxt), "window": win})
            before = grab()
            if box:
                box.click_input()
            elif lst:
                r = lst.rectangle()
                for dy in (30, 12, 48, 66):
                    win32api.SetCursorPos((r.left + 26, r.top + dy))
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
                    time.sleep(0.6)
                    if region_changed(before, grab(), (r.left, r.top, r.right, r.bottom)):
                        break
            time.sleep(0.6)
            shot("tasks_ticked", {"checkbox": target, "next": rect(nxt), "window": win})
            nxt.click_input(); done.add("tasks")
        elif "Ready to Install" in texts and "ready" not in done:
            inst = find_desc(wiz, lambda t: t.strip() == "Install", "Button")
            shot("ready", {"install": rect(inst), "window": win})
            inst.click_input(); done.add("ready")
        elif "Installing" in texts and "installing" not in done:
            shot("installing", {"window": win}); done.add("installing")
        elif "Finish" in texts:
            fin = find_desc(wiz, lambda t: t.strip() == "Finish", "Button")
            lst = None
            for c in wiz.descendants():
                if "CheckListBox" in (c.class_name() or "") or "Open Provenance Wiki" in c.window_text():
                    lst = c
                    break
            shot("finish", {"open_now": rect(lst), "finish": rect(fin), "window": win})
            fin.click_input()
            log("wizard finished: " + ", ".join(sorted(done)))
            return
    log("wizard loop ended without Finish; pages: " + ", ".join(sorted(done)))


# ── 2. The app in Edge ────────────────────────────────────────────────────────

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def edge_window():
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "Chrome_WidgetWin_1" \
                and "Edge" in win32gui.GetWindowText(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def content_origin():
    top = edge_window()
    best = []

    def cb(h, _):
        if win32gui.GetClassName(h) == "Chrome_RenderWidgetHostHWND" and win32gui.IsWindowVisible(h):
            l, t, r, b = win32gui.GetWindowRect(h)
            best.append(((r - l) * (b - t), l, t))
    if top:
        win32gui.EnumChildWindows(top, cb, None)
    if best:
        _, l, t = max(best)
        return l, t
    return 0, 0


def focus_edge():
    h = edge_window()
    if h:
        try:
            UIA.window(handle=h).set_focus()
        except Exception:
            pass
    time.sleep(0.6)


def box_of(page, locator):
    b = locator.bounding_box(timeout=8000)
    if not b:
        return None
    z = page.evaluate("window.devicePixelRatio")
    ox, oy = content_origin()
    return [ox + b["x"] * z, oy + b["y"] * z, ox + (b["x"] + b["width"]) * z, oy + (b["y"] + b["height"]) * z]


def to_center(page, locator):
    locator.evaluate("e => e.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(700)


def section(page, heading_text):
    return page.locator("section", has=page.locator("h2", has_text=heading_text)).first


def app_flow():
    from playwright.sync_api import sync_playwright
    cfg = Path(os.environ["LOCALAPPDATA"]) / "ProvenanceWiki Config" / "instance.json"
    for _ in range(120):
        if cfg.exists():
            break
        time.sleep(0.5)
    port = json.loads(cfg.read_text())["port"]
    url = f"http://127.0.0.1:{port}/"
    log(f"app at {url}")
    subprocess.Popen([EDGE, r"--user-data-dir=C:\edge-video", "--no-first-run", "--no-default-browser-check",
                      "--start-maximized", "--remote-debugging-port=9222", "--disable-features=msEdgeSidebarV2",
                      url])
    time.sleep(6)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        page.wait_for_url("**/settings?welcome=1", timeout=30000)
        page.wait_for_selector("text=Create my archive here", timeout=30000)
        page.wait_for_timeout(1500)
        focus_edge()
        keyboard.send_keys("^{=}")
        time.sleep(0.4)
        keyboard.send_keys("^{=}")          # 125%
        time.sleep(1)
        log(f"page zoom: devicePixelRatio={page.evaluate('window.devicePixelRatio')}")
        for fn in (welcome, archive, keys, step3, step4):
            step(fn)(page)
        browser.close()


def welcome(page):
    s1 = section(page, "Where to store your archive")
    shot("welcome", {"heading": box_of(page, page.locator("h1")), "step1": box_of(page, s1)})
    btn = page.get_by_role("button", name="Create my archive here")
    shot("archive_create", {"button": box_of(page, btn), "step1": box_of(page, s1)})


def archive(page):
    s1 = section(page, "Where to store your archive")
    other = page.get_by_text("Choose another folder", exact=True).first
    other_box = box_of(page, other.locator("xpath=ancestor::label[1]"))
    other.click()
    dlg = find_window(lambda t, c: c == "#32770", 15)
    time.sleep(1.5)
    shot("archive_other", {"button": other_box, "dialog": rect(dlg) if dlg else None})
    if dlg:
        cancel = find_desc(dlg, lambda t: t.strip() == "Cancel", "Button")
        (cancel.click_input() if cancel else dlg.close())
    focus_edge()
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
    page.wait_for_timeout(1500)
    v_in.fill("pa-DEMO-not-a-real-key-000000000000")
    v_row = v_in.locator("xpath=..")
    v_row.get_by_role("button", name="Save").click()
    page.wait_for_timeout(1500)
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


def find_key(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str):
                return v
            r = find_key(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r:
                return r
    return None


def step4(page):
    s = page.locator("#add-photos")
    to_center(page, s.get_by_role("button", name="Open photos folder"))
    open_btn = s.get_by_role("button", name="Open photos folder")
    shot("step4_open", {"button": box_of(page, open_btn), "heading": box_of(page, s.locator("h2"))})
    settings = page.evaluate("() => fetch('/api/settings').then(r => r.json())")
    photos = Path(find_key(settings, "photos"))
    log(f"photos folder: {photos}")
    open_btn.click()
    ex = find_window(lambda t, c: c == "CabinetWClass", 20)
    time.sleep(2)
    make_document_photos(photos)
    if ex:
        win32gui.MoveWindow(ex.handle, 220, 120, 1440, 800, True)
        ex.set_focus()
        time.sleep(1)
        keyboard.send_keys("^+2")  # Large icons, so the photographs show
        time.sleep(4)
        shot("photos_folder", {"window": rect(ex)})
        ex.close()
    else:
        shot("photos_folder")
    focus_edge()
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
    shot("source", {"input": box_of(page, src), "label": box_of(page, s.get_by_text("Say where these documents", exact=False))})

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


# ── 3. Tray icon and Start menu ──────────────────────────────────────────────

def find_anywhere(pred, control_type="Button"):
    for w in UIA.windows():
        try:
            c = find_desc(w, pred, control_type)
            if c:
                return c
        except Exception:
            pass
    return None


def is_ours(t):
    return "Provenance" in (t or "")


@step
def tray():
    bar = UIA.window(class_name="Shell_TrayWnd")
    clock = find_desc(bar, lambda t: ":" in t and ("AM" in t or "PM" in t))
    icon = find_desc(bar, is_ours, "Button")
    if not icon:
        chevron = find_desc(bar, lambda t: "hidden icons" in t.lower() or "chevron" in t.lower(), "Button")
        shot("tray", {"chevron": rect(chevron), "clock": rect(clock)})
        chevron.click_input()
        time.sleep(2)
        icon = find_anywhere(is_ours)
        log("overflow windows: " + "; ".join(window_list()))
        shot("tray_open", {"icon": rect(icon)})
    else:
        shot("tray", {"icon": rect(icon), "clock": rect(clock)})
    if not icon:
        log("tray icon not found")
        return
    icon.right_click_input()
    time.sleep(1.5)
    r = None
    hwnd = win32gui.FindWindow("#32768", None)
    if hwnd:
        r = list(win32gui.GetWindowRect(hwnd))
    shot("tray_menu", {"menu": r, "icon": rect(icon)})
    keyboard.send_keys("{ESC}")
    time.sleep(0.5)
    keyboard.send_keys("{ESC}")


@step
def start_menu():
    keyboard.send_keys("{VK_LWIN}")
    time.sleep(3)
    item = find_anywhere(is_ours, None)
    shot("start_menu", {"item": rect(item)})
    keyboard.send_keys("Provenance", with_spaces=True, pause=0.08)
    time.sleep(8)
    item = find_anywhere(is_ours, None)
    shot("start_search", {"item": rect(item)})
    keyboard.send_keys("{ESC}")


if __name__ == "__main__":
    # A hang prints every thread's stack and exits, instead of a silent CI timeout.
    faulthandler.dump_traceback_later(14 * 60, exit=True)
    os.environ["PW_NO_BROWSER"] = "1"   # the video opens its own Edge window
    set_resolution()
    tidy_desktop()
    downloads_and_launch()
    wizard()
    step(app_flow)()
    tray()
    start_menu()
    log("windows at end: " + "; ".join(window_list()))
    log("done")
