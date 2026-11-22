"""Assemble the Windows install video from the CI screenshots and narration.

    python docs/video/compose.py <screens_dir> <narration_dir> <out.mp4> [--platform windows|mac]
                                 [--only id,id] [--stills]

screens_dir: output of desktop/windows/capture_install_video.py (PNGs + meta.json)
narration_dir: one WAV per scene id (docs/video/make_narration.py)
--stills: write one PNG per scene at its busiest moment instead of rendering.
"""
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
W, H, FPS = 1920, 1080, 30
NAVY_DEEP, NAVY, NAVY_MID = (15, 25, 35), (26, 35, 50), (36, 48, 68)
CREAM, GOLD, GOLD_LIGHT = (245, 240, 232), (201, 168, 76), (232, 204, 122)
HIL = (240, 176, 40)
CAPTION_H = 150
LEAD_IN, TAIL = 0.45, 0.95

F = "/usr/share/fonts/opentype/inter/Inter-{}.otf"
SERIF = "/usr/share/fonts/truetype/google-fonts/ofl/crimsontext/CrimsonText-{}.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


CAP_FONT = font(F.format("SemiBold"), 46)
CHIP_FONT = font(F.format("SemiBold"), 26)

# Which screenshot(s) each scene shows: (shot, focus box keys, highlight keys)
SEGMENTS = {
    "downloads":      [("downloads", "window", ["file"])],
    "smartscreen1":   [("smartscreen1", "window", ["more_info"])],
    "smartscreen2":   [("smartscreen2", "window", ["run_anyway"])],
    "license":        [("license", "window", ["accept"]), ("license_accepted", "window", ["next"])],
    "infobefore":     [("infobefore", "window", ["next"])],
    "selectdir":      [("selectdir", "window", ["next"])],
    "tasks":          [("tasks", "window", ["checkbox"]), ("tasks_ticked", "window", ["next"])],
    "ready":          [("ready", "window", ["install"])],
    "finish":         [("finish", "window", ["open_now"]), ("finish", "window", ["finish"])],
    "welcome":        [("welcome", None, []), ("welcome", "step1", ["step1"])],
    "archive_create": [("archive_create", "step1", ["button"])],
    "archive_other":  [("archive_other", "button", ["button"]), ("archive_other", "dialog", ["dialog"])],
    "archive_done":   [("archive_done", "section", ["section"])],
    "keys_paste":     [("keys_paste", "section", ["input"]), ("keys_paste", "section", ["save"])],
    "keys_test":      [("keys_test", "test1+ok2", ["test1", "test2"]), ("keys_test", "test1+ok2", ["ok1", "ok2"])],
    "step3":          [("step3", "section", ["section"])],
    "step4_open":     [("step4_open", "heading+button", ["button"])],
    "photos_folder":  [("photos_folder", "window", [])],
    "check_again":    [("check_again", "link+status", ["link"]), ("check_again", "link+status", ["status"])],
    "source":         [("source", "label+input", ["input"])],
    "ingest":         [("ingest", "button+result", ["button"]), ("ingest", "button+result", ["result"])],
    "tray":           [("tray", "chevron+clock", ["chevron"]), ("tray_open", "icon+clock", ["icon"])],
    "tray_menu":      [("tray_menu", "menu+icon", ["menu"])],
    "start_menu":     [("start_menu", [600, 170, 1330, 1040], ["item"])],
}
PARTS = [("Part 1 of 3 · Install", ["download", "downloads", "smartscreen1", "smartscreen2", "license",
                                    "infobefore", "selectdir", "tasks", "ready", "finish"]),
         ("Part 2 of 3 · Set up", ["welcome", "archive_create", "archive_other", "archive_done", "keys_paste",
                                   "keys_test", "step3", "step4_open", "photos_folder", "check_again", "source",
                                   "ingest"]),
         ("Part 3 of 3 · Everyday use", ["tray", "tray_menu", "start_menu"])]

SHARED_APP = {k: SEGMENTS[k] for k in ("welcome", "archive_create", "archive_other", "archive_done", "keys_paste",
                                       "keys_test", "step3", "step4_open", "photos_folder", "check_again",
                                       "source", "ingest")}
MAC_SEGMENTS = dict(SHARED_APP, **{
    "downloads":  [("downloads", "window", [])],
    "dmg":        [("dmg", "window", [])],
    "applications": [("applications", "window", [])],
    "menubar":    [("menubar", "icon", ["icon"])],
    "menu":       [("menu", "menu+icon", ["menu"])],
    "reopen":     [("reopen", "window", [])],
})
MAC_PARTS = [("Part 1 of 3 · Install", ["download", "downloads", "dmg", "applications", "gatekeeper", "settings",
                                        "password"]),
             ("Part 2 of 3 · Set up", PARTS[1][1]),
             ("Part 3 of 3 · Everyday use", ["menubar", "menu", "reopen"])]
PLATFORM = {
    "windows": {"narration": "narration.json", "segments": SEGMENTS, "parts": PARTS, "on": "on Windows"},
    "mac": {"narration": "narration_mac.json", "segments": MAC_SEGMENTS, "parts": MAC_PARTS, "on": "on a Mac"},
}
CUR = PLATFORM["windows"]
PART_OF = {}


def use_platform(name):
    global CUR, SEGMENTS, PART_OF
    CUR = PLATFORM[name]
    SEGMENTS = CUR["segments"]
    PART_OF = {sid: label for label, ids in CUR["parts"] for sid in ids}


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def wav_seconds(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def wrap(text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if fnt.getlength(t) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


# ── Overlays ──────────────────────────────────────────────────────────────────

def caption_layer(text, top=False):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lines = wrap(text, CAP_FONT, W - 260)
    h = 56 * len(lines) + 70
    y0 = 0 if top else H - h
    d.rectangle((0, y0, W, y0 + h), fill=NAVY + (235,))
    d.rectangle((0, y0 + (h - 6 if top else 0), W, y0 + (h if top else 6)), fill=GOLD + (255,))
    y = y0 + 35 + (0 if top else 4)
    for ln in lines:
        d.text((W / 2, y + 26), ln, font=CAP_FONT, fill=(255, 255, 255, 255), anchor="mm")
        y += 56
    return layer, h


def chip_layer(label, top_offset=0):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if not label:
        return layer
    d = ImageDraw.Draw(layer)
    tw = CHIP_FONT.getlength(label)
    x0, y0 = 36, 30 + top_offset
    d.rounded_rectangle((x0, y0, x0 + tw + 44, y0 + 52), radius=26, fill=NAVY + (225,))
    d.text((x0 + 22, y0 + 26), label, font=CHIP_FONT, fill=GOLD_LIGHT + (255,), anchor="lm")
    return layer


def highlight_layer(boxes, alpha, pulse):
    """Dim everything except the boxes, and draw a gold outline around each."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, int(110 * alpha)))
    d = ImageDraw.Draw(layer)
    for b in boxes:
        x0, y0, x1, y1 = b
        d.rounded_rectangle((x0 - 14, y0 - 12, x1 + 14, y1 + 12), radius=14, fill=(0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    a = int(255 * alpha)
    for b in boxes:
        x0, y0, x1, y1 = b
        grow = 3 * pulse
        g.rounded_rectangle((x0 - 14 - grow, y0 - 12 - grow, x1 + 14 + grow, y1 + 12 + grow),
                            radius=14, outline=HIL + (a,), width=7)
    blur = glow.filter(ImageFilter.GaussianBlur(9))
    layer.alpha_composite(blur)
    layer.alpha_composite(glow)
    return layer


# ── Screenshot scenes ─────────────────────────────────────────────────────────

class Shots:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))["shots"]
        self.cache = {}

    def has(self, sid):
        return sid in self.meta and (self.folder / self.meta[sid]["file"]).exists()

    def image(self, sid):
        if sid not in self.cache:
            img = Image.open(self.folder / self.meta[sid]["file"]).convert("RGB")
            self.scale = W / img.size[0]
            if img.size != (W, H):
                img = img.resize((W, round(img.size[1] * W / img.size[0])), Image.LANCZOS)
                canvas = Image.new("RGB", (W, H), NAVY_DEEP)
                canvas.paste(img, (0, 0))
                img = canvas
            self.cache[sid] = img
        return self.cache[sid]

    def box(self, sid, spec):
        if not spec:
            return None
        if isinstance(spec, list):          # explicit screen rectangle
            return [float(v) * W / self.meta[sid]["size"][0] for v in spec]
        boxes = self.meta[sid]["boxes"]
        src = self.meta[sid]["size"][0]
        k = W / src
        found = [boxes[x] for x in spec.split("+") if x in boxes]
        if not found:
            return None
        x0 = min(b[0] for b in found); y0 = min(b[1] for b in found)
        x1 = max(b[2] for b in found); y1 = max(b[3] for b in found)
        return [x0 * k, y0 * k, x1 * k, y1 * k]


def crop_for(box, caption_h):
    """A 16:9 crop around box, leaving room for the caption, zoom at most 1.9x."""
    if not box:
        return (0.0, 0.0, float(W), float(H))
    x0, y0, x1, y1 = box
    pad = 60
    bw, bh = (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad
    usable = (H - caption_h) / H
    cw = max(bw, bh / usable * W / H, W / 1.9)
    cw = min(cw, W)
    ch = cw * H / W
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2 + (caption_h / 2) * (ch / H)   # centre in the area above the caption
    left = min(max(cx - cw / 2, 0), W - cw)
    top = min(max(cy - ch / 2, 0), H - ch)
    return (left, top, left + cw, top + ch)


def lerp_crop(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def map_box(box, crop):
    l, t, r, b = crop
    sx, sy = W / (r - l), H / (b - t)
    return [(box[0] - l) * sx, (box[1] - t) * sy, (box[2] - l) * sx, (box[3] - t) * sy]


class Renderer:
    def __init__(self, out):
        self.out = Path(out)

    def encode(self, frames, path, n):
        tmp = path.with_name(path.stem + ".part.mp4")
        p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                              "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                              "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), str(tmp)], stdin=subprocess.PIPE)
        for i in range(n):
            p.stdin.write(frames(i).tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg failed for {path}")
        tmp.rename(path)


def screen_scene(shots, sid, caption, dur, start_crop):
    segs = [s for s in SEGMENTS[sid] if shots.has(s[0])]
    n = round(dur * FPS)
    per = n / len(segs)
    plans = []
    prev = start_crop
    cap_top = False
    for shot_id, focus, hl in segs:
        fbox = shots.box(shot_id, focus)
        target = crop_for(fbox, CAPTION_H)
        hboxes = [shots.box(shot_id, h) for h in hl]
        hboxes = [b for b in hboxes if b]
        plans.append((shot_id, prev, target, hboxes))
        for b in hboxes:   # caption would cover the highlight → put it at the top
            if map_box(b, target)[3] > H - CAPTION_H - 20:
                cap_top = True
        prev = target
    cap, cap_h = caption_layer(caption, top=cap_top)
    chip = chip_layer(PART_OF.get(sid, ""), top_offset=cap_h if cap_top else 0)

    def frame(i):
        k = min(int(i // per), len(plans) - 1)
        shot_id, a, b, hboxes = plans[k]
        t = (i - k * per) / FPS
        move = ease(t / 1.1)
        crop = lerp_crop(a, b, move)
        # slow drift after the move, so the picture never looks frozen
        drift = 1 + 0.015 * max(0.0, t - 1.1)
        cx, cy = (crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2
        hw, hh = (crop[2] - crop[0]) / 2 / drift, (crop[3] - crop[1]) / 2 / drift
        crop = (cx - hw, cy - hh, cx + hw, cy + hh)
        img = shots.image(shot_id).resize((W, H), Image.BICUBIC, box=crop).convert("RGBA")
        if hboxes:
            alpha = ease((t - 1.0) / 0.5)
            if alpha > 0:
                pulse = 0.5 + 0.5 * math.sin((t - 1.0) * 4)
                img.alpha_composite(highlight_layer([map_box(bb, crop) for bb in hboxes], alpha, pulse))
        img.alpha_composite(chip)
        img.alpha_composite(cap)
        return img.convert("RGB")

    return frame, n, plans[-1][2]


# ── Title cards and illustrations ────────────────────────────────────────────

def card_base():
    img = Image.new("RGB", (W, H), NAVY_DEEP)
    d = ImageDraw.Draw(img)
    for y in range(H):   # soft vertical gradient
        c = tuple(int(NAVY_DEEP[i] + (NAVY_MID[i] - NAVY_DEEP[i]) * (y / H) ** 1.5) for i in range(3))
        d.line((0, y, W, y), fill=c)
    return img


def icon(size):
    p = HERE.parent.parent / "build" / "icon.png"
    if p.exists():
        return Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
    return None


def fade(img, i, n):
    a = min(1.0, i / 12, (n - i) / 10)
    if a >= 1:
        return img
    return Image.blend(Image.new("RGB", (W, H), NAVY_DEEP), img, max(0.0, a))


def card_scene(sid, caption, dur):
    n = round(dur * FPS)
    title = font(SERIF.format("SemiBold"), 104)
    sub = font(F.format("Regular"), 40)
    big = font(F.format("SemiBold"), 50)
    base = card_base()
    d = ImageDraw.Draw(base)
    items = []
    if sid == "intro":
        ic = icon(170)
        if ic:
            base.paste(ic, (W // 2 - 85, 210), ic)
        d.text((W / 2, 480), "Installing Provenance Wiki", font=title, fill=GOLD_LIGHT, anchor="mm")
        d.text((W / 2, 570), CUR["on"] + "  ·  testing version", font=sub, fill=CREAM, anchor="mm")
        d.text((W / 2, 700), "About 10 minutes   ·   No technical knowledge needed", font=sub,
               fill=(190, 200, 214), anchor="mm")
    elif sid == "need":
        d.text((W / 2, 200), "Before you start", font=title, fill=GOLD_LIGHT, anchor="mm")
        items = (["The Provenance Wiki Setup file", "A few photographs of test documents",
                  "Two API keys: Anthropic and Voyage AI"] if CUR is PLATFORM["windows"] else
                 ["A Mac with Apple silicon, macOS 15 or later", "The Provenance Wiki .dmg file",
                  "A few photographs of test documents", "Two API keys: Anthropic and Voyage AI"])
    elif sid == "outro":
        ic = icon(140)
        if ic:
            base.paste(ic, (W // 2 - 70, 190), ic)
        d.text((W / 2, 430), "That’s it!", font=title, fill=GOLD_LIGHT, anchor="mm")
        d.text((W / 2, 560), "Something went wrong?", font=big, fill=CREAM, anchor="mm")
        d.text((W / 2, 630), "Send us a screenshot and a short note.", font=sub, fill=CREAM, anchor="mm")
        d.text((W / 2, 780), "Thank you for testing Provenance Wiki", font=font(SERIF.format("Italic"), 56),
               fill=GOLD, anchor="mm")
    elif sid == "download" and CUR is PLATFORM["mac"]:
        draw_mac_download(base)
    elif sid == "download":
        draw_download(base)
    elif sid in ("gatekeeper", "settings", "password"):
        draw_mac_security(base, sid)
    elif sid in ("smartscreen1", "smartscreen2"):
        draw_smartscreen(base, sid)

    times = [0.1 + (0.8 / max(1, len(items))) * k for k in range(len(items))]
    cap, _ = caption_layer(caption) if sid not in ("intro", "need", "outro") else (None, 0)

    def frame(i):
        img = base.copy()
        if items:
            dd = ImageDraw.Draw(img)
            for k, text in enumerate(items):
                a = ease((i / n - times[k]) / 0.06)
                if a <= 0:
                    continue
                y = (420 if len(items) == 3 else 360) + k * (170 if len(items) == 3 else 150)
                x = 470 - int(30 * (1 - a))
                col = tuple(int(NAVY_DEEP[j] + (c - NAVY_DEEP[j]) * a) for j, c in enumerate(GOLD))
                dd.ellipse((x, y - 45, x + 90, y + 45), fill=col)
                dd.text((x + 45, y), str(k + 1), font=big, fill=NAVY_DEEP, anchor="mm")
                tc = tuple(int(NAVY_DEEP[j] + (c - NAVY_DEEP[j]) * a) for j, c in enumerate(CREAM))
                dd.text((x + 130, y), text, font=big, fill=tc, anchor="lm")
        if cap is not None:
            img = img.convert("RGBA")
            img.alpha_composite(cap)
            img = img.convert("RGB")
        return fade(img, i, n)

    return frame, n


def button(d, box, label, fnt, primary=False, hl=False):
    x0, y0, x1, y1 = box
    if hl:
        d.rounded_rectangle((x0 - 12, y0 - 12, x1 + 12, y1 + 12), radius=12, outline=HIL, width=7)
    d.rounded_rectangle(box, radius=6, fill=(0, 95, 184) if primary else (255, 255, 255),
                        outline=(140, 140, 140), width=2)
    d.text(((x0 + x1) / 2, (y0 + y1) / 2), label, font=fnt,
           fill=(255, 255, 255) if primary else (30, 30, 30), anchor="mm")


def label(d, text):
    d.text((W / 2, 90), text, font=font(F.format("Medium"), 30), fill=(170, 182, 200), anchor="mm")


def draw_download(img):
    """A simplified drawing (not a screenshot) of a browser download warning."""
    d = ImageDraw.Draw(img)
    label(d, "Illustration: your browser’s message may look a little different")
    d.rounded_rectangle((460, 250, 1460, 760), radius=18, fill=(250, 250, 250))
    f = font(F.format("Regular"), 34)
    fb = font(F.format("SemiBold"), 34)
    d.rounded_rectangle((520, 310, 600, 400), radius=8, fill=(220, 226, 236))
    d.text((560, 355), "EXE", font=font(F.format("Bold"), 24), fill=(60, 70, 90), anchor="mm")
    d.text((630, 330), "ProvenanceWiki-Setup-…-TESTING….exe", font=fb, fill=(25, 25, 25))
    d.text((630, 382), "isn’t commonly downloaded.", font=f, fill=(170, 60, 20))
    d.text((520, 470), "Make sure you trust this file before you open it.", font=f, fill=(70, 70, 70))
    button(d, (520, 600, 760, 680), "Keep", fb, primary=True, hl=True)
    button(d, (800, 600, 1040, 680), "Delete", fb)


def draw_smartscreen(img, sid):
    """A simplified drawing (not a screenshot) of the Windows SmartScreen box."""
    d = ImageDraw.Draw(img)
    label(d, "Illustration: what Windows shows the first time")
    d.rectangle((410, 170, 1510, 830), fill=(0, 86, 160))
    d.text((480, 250), "Windows protected your PC", font=font(F.format("Regular"), 62), fill=(255, 255, 255))
    f = font(F.format("Regular"), 32)
    d.text((480, 370), "Microsoft Defender SmartScreen prevented an unrecognised", font=f, fill=(225, 235, 245))
    d.text((480, 415), "app from starting. Running this app might put your PC at risk.", font=f,
           fill=(225, 235, 245))
    fb = font(F.format("SemiBold"), 32)
    d.text((480, 490), "More info", font=fb, fill=(255, 255, 255))
    d.line((480, 530, 480 + fb.getlength("More info"), 530), fill=(255, 255, 255), width=2)
    if sid == "smartscreen1":
        x1 = 480 + fb.getlength("More info")
        d.rounded_rectangle((466, 478, x1 + 14, 545), radius=10, outline=HIL, width=7)
        button(d, (1250, 730, 1450, 800), "Don’t run", fb)
    else:
        d.text((480, 570), "App:  ProvenanceWiki-Setup-…-TESTING….exe", font=f, fill=(225, 235, 245))
        d.text((480, 615), "Publisher:  Unknown publisher", font=f, fill=(225, 235, 245))
        button(d, (1000, 730, 1220, 800), "Run anyway", fb, hl=True)
        button(d, (1250, 730, 1450, 800), "Don’t run", fb)


def mac_alert(d, box):
    d.rounded_rectangle(box, radius=26, fill=(236, 236, 238), outline=(200, 200, 205), width=2)


def draw_mac_download(img):
    """A simplified drawing (not a screenshot) of a browser asking to allow downloads."""
    d = ImageDraw.Draw(img)
    label(d, "Illustration: your browser’s message may look a little different")
    mac_alert(d, (500, 300, 1420, 700))
    fb = font(F.format("SemiBold"), 36)
    f = font(F.format("Regular"), 26)
    d.text((W / 2, 400), "Do you want to allow downloads", font=fb, fill=(25, 25, 25), anchor="mm")
    d.text((W / 2, 450), "from this site?", font=fb, fill=(25, 25, 25), anchor="mm")
    d.text((W / 2, 520), "You can change which websites can download files in Settings.", font=f,
           fill=(90, 90, 90), anchor="mm")
    button(d, (620, 590, 940, 660), "Cancel", fb)
    button(d, (980, 590, 1300, 660), "Allow", fb, primary=True, hl=True)


def app_icon_at(img, x, y, size):
    ic = icon(size)
    if ic:
        img.paste(ic, (x, y), ic)


def draw_mac_security(img, sid):
    """Simplified drawings (not screenshots) of the first-open check in macOS 15."""
    d = ImageDraw.Draw(img)
    label(d, "Illustration: the exact wording on your Mac may differ")
    fb = font(F.format("SemiBold"), 34)
    f = font(F.format("Regular"), 28)
    name = "Provenance Wiki (Testing)"
    if sid == "gatekeeper":
        mac_alert(d, (610, 170, 1310, 900))
        app_icon_at(img, 900, 220, 120)
        d.text((W / 2, 400), f"“{name}” Not Opened", font=fb, fill=(25, 25, 25), anchor="mm")
        for k, ln in enumerate(["Apple could not verify “Provenance Wiki", "(Testing)” is free of malware that may",
                                "harm your Mac or compromise your privacy."]):
            d.text((W / 2, 470 + 40 * k), ln, font=f, fill=(60, 60, 60), anchor="mm")
        button(d, (680, 650, 1240, 725), "Done", fb, primary=True, hl=True)
        button(d, (680, 750, 1240, 825), "Move to Trash", fb)
        d.line((660, 787, 1260, 787), fill=(200, 40, 40), width=6)
        d.text((1290, 787), "Don’t click this", font=fb, fill=(240, 120, 110), anchor="lm")
    elif sid == "settings":
        d.rounded_rectangle((300, 150, 1620, 930), radius=22, fill=(246, 246, 248))
        d.rounded_rectangle((300, 150, 720, 930), radius=22, fill=(228, 228, 232))
        d.rectangle((700, 150, 720, 930), fill=(228, 228, 232))
        for k, item in enumerate(["Wi-Fi", "Bluetooth", "Network", "General", "Appearance",
                                  "Privacy & Security", "Desktop & Dock", "Displays"]):
            y = 250 + k * 70
            if item == "Privacy & Security":
                d.rounded_rectangle((325, y - 28, 695, y + 28), radius=10, fill=(0, 110, 220))
                d.rounded_rectangle((315, y - 38, 705, y + 38), radius=14, outline=HIL, width=6)
            d.text((350, y), item, font=f, fill=(255, 255, 255) if item == "Privacy & Security" else (40, 40, 40),
                   anchor="lm")
        d.text((780, 230), "Privacy & Security", font=font(F.format("Bold"), 40), fill=(25, 25, 25), anchor="lm")
        d.text((780, 380), "Security", font=fb, fill=(25, 25, 25), anchor="lm")
        d.rounded_rectangle((770, 420, 1570, 640), radius=14, fill=(255, 255, 255), outline=(215, 215, 220))
        d.text((810, 480), f"“{name}” was blocked to", font=f, fill=(40, 40, 40), anchor="lm")
        d.text((810, 520), "protect your Mac.", font=f, fill=(40, 40, 40), anchor="lm")
        button(d, (1300, 560, 1540, 620), "Open Anyway", fb, hl=True)
        d.text((780, 720), "↓  scroll down to find this", font=f, fill=(120, 120, 130), anchor="lm")
    else:
        mac_alert(d, (300, 260, 900, 820))
        d.text((600, 340), "Enter your Mac password", font=fb, fill=(25, 25, 25), anchor="mm")
        d.text((600, 390), "to allow this.", font=f, fill=(60, 60, 60), anchor="mm")
        d.rounded_rectangle((360, 470, 840, 540), radius=10, fill=(255, 255, 255), outline=(170, 170, 180), width=2)
        d.text((385, 505), "••••••••", font=fb, fill=(40, 40, 40), anchor="lm")
        d.rounded_rectangle((350, 460, 850, 550), radius=14, outline=HIL, width=6)
        button(d, (560, 680, 840, 750), "OK", fb, primary=True)
        d.text((W / 2, 540), "then", font=font(F.format("SemiBold"), 44), fill=GOLD_LIGHT, anchor="mm")
        mac_alert(d, (1020, 260, 1620, 820))
        app_icon_at(img, 1260, 300, 100)
        d.text((1320, 460), f"Open “{name}”?", font=fb, fill=(25, 25, 25), anchor="mm")
        d.text((1320, 520), "macOS can’t verify the developer.", font=f, fill=(60, 60, 60), anchor="mm")
        button(d, (1080, 600, 1560, 670), "Open Anyway", fb, primary=True, hl=True)
        button(d, (1080, 700, 1560, 770), "Move to Trash", fb)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    screens, narr, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    stills = "--stills" in sys.argv
    use_platform(sys.argv[sys.argv.index("--platform") + 1] if "--platform" in sys.argv else "windows")
    spec = json.loads((HERE / CUR["narration"]).read_text(encoding="utf-8"))
    shots = Shots(screens)
    work = out.parent / (out.stem + "-scenes")
    work.mkdir(parents=True, exist_ok=True)
    r = Renderer(out)
    files, audio, prev_crop, prev_part = [], [], None, None
    for sc in spec["scenes"]:
        sid = sc["id"]
        wav = narr / f"{sid}.wav"
        speech = wav_seconds(wav)
        dur = LEAD_IN + speech + TAIL
        use_shots = sid in SEGMENTS and any(shots.has(s[0]) for s in SEGMENTS[sid])
        if use_shots:
            part = PART_OF.get(sid)
            start = prev_crop if (prev_crop and part == prev_part) else (0.0, 0.0, float(W), float(H))
            frame, n, prev_crop = screen_scene(shots, sid, sc["caption"], dur, start)
            prev_part = part
        else:
            frame, n = card_scene(sid, sc["caption"], dur)
            prev_crop, prev_part = None, None
        audio.append((wav, n / FPS))
        path = work / f"{len(files):02d}-{sid}.mp4"
        files.append(path)
        if stills:
            frame(int(n * 0.8)).save(work / f"{len(files) - 1:02d}-{sid}.jpg", quality=90)
            continue
        if only and sid not in only and path.exists():
            continue
        r.encode(frame, path, n)
        print(f"{sid}: {n / FPS:.1f}s", flush=True)
    if stills:
        return
    # Audio: each scene's speech after LEAD_IN, padded to the scene's length.
    chunks = []
    for wav, seconds in audio:
        with wave.open(str(wav)) as w:
            sr = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        total = round(seconds * sr)
        pre = np.zeros(round(LEAD_IN * sr), np.int16)
        seg = np.concatenate([pre, data])[:total]
        chunks.append(np.concatenate([seg, np.zeros(total - len(seg), np.int16)]))
    track = work / "narration.wav"
    with wave.open(str(track), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.concatenate(chunks).tobytes())
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{f.name}'\n" for f in files))
    silent = work / "video.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(silent)], check=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(track),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-af", "loudnorm=I=-16:TP=-1.5", "-movflags", "+faststart", "-shortest", str(tmp)], check=True)
    tmp.rename(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
