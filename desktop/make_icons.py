"""Generate app icons (build/icon.png, .icns, .ico) — an original gold "P" on navy."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1] if len(sys.argv) > 1 else "build")
out.mkdir(parents=True, exist_ok=True)
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
navy, gold, cream = (27, 38, 59, 255), (201, 168, 76, 255), (243, 238, 226, 255)
d.rounded_rectangle((64, 64, S - 64, S - 64), radius=200, fill=navy)
d.rounded_rectangle((110, 110, S - 110, S - 110), radius=160, outline=gold, width=14)
# letter P from simple shapes
d.rectangle((330, 250, 430, 780), fill=gold)
d.ellipse((330, 240, 720, 560), fill=gold)
d.ellipse((430, 330, 620, 470), fill=navy)
d.rectangle((330, 330, 430, 470), fill=gold)
# small archive "page" mark
d.rectangle((600, 620, 720, 760), fill=cream)
for y in (650, 685, 720):
    d.line((620, y, 700, y), fill=navy, width=10)
img.save(out / "icon.png")
img.save(out / "icon.icns")
img.save(out / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons written to", out)
