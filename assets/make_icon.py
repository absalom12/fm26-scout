"""Draw the app icon (three-stroke S in the mode colours) and write icon.png / icon.ico / icon.icns. Run: python assets/make_icon.py"""
import os, subprocess, sys, tempfile
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
K = 4  # supersampling factor
FULL, FAIR, MONEY = "#3d9bff", "#2fe08a", "#ffc93c"

# Same geometry as the site's brand SVGs (scoutsheet-site/brand/app-icon/*.svg).
MAC = {  # 1024 grid: macOS icon template, 824px rounded square with 100px margins
    "size": 1024, "bg": (100, 100, 924, 924, 185, "#11171e"), "rim": "#2a3642",
    "S": [(FULL, [(411.9, 277), (638.8, 277), (686.5, 333.7), (681.4, 366.1), (486.9, 366.1), (476, 435), (386.9, 435)]),
          (FAIR, [(381.7, 467.4), (665.4, 467.4), (651.2, 556.6), (367.6, 556.6)]),
          (MONEY, [(557, 589), (646.1, 589), (621.1, 747), (337.5, 747), (342.6, 714.6), (408.3, 657.9), (546.1, 657.9)])],
}
TILE = {  # 256 grid: full-bleed tile, so the S stays legible at 16-24px in the Windows taskbar
    "size": 256, "bg": (0, 0, 256, 256, 48, "#07090c"), "rim": None,
    "S": [(FULL, [(87.7, 33.3), (179.1, 33.3), (198.4, 56.1), (196.3, 69.2), (117.9, 69.2), (113.5, 97), (77.6, 97)]),
          (FAIR, [(75.5, 110), (189.8, 110), (184.1, 146), (69.8, 146)]),
          (MONEY, [(146.1, 159), (182.1, 159), (172, 222.7), (57.6, 222.7), (59.7, 209.7), (86.2, 186.8), (141.7, 186.8)])],
}


def draw(spec, px):
    n = px * K
    f = n / spec["size"]
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1, r, fill = spec["bg"]
    box = [x0 * f, y0 * f, x1 * f - 1, y1 * f - 1]
    d.rounded_rectangle(box, radius=r * f, fill=fill)
    if spec["rim"]:
        d.rounded_rectangle(box, radius=r * f, outline=spec["rim"], width=max(1, round(3 * f)))
    for col, pts in spec["S"]:
        d.polygon([(x * f, y * f) for x, y in pts], fill=col)
    return img.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    draw(MAC, 1024).save(os.path.join(HERE, "icon.png"))
    tiles = [draw(TILE, s) for s in (256, 128, 64, 48, 32, 24, 16)]
    tiles[0].save(os.path.join(HERE, "icon.ico"), sizes=[t.size for t in tiles], append_images=tiles[1:])
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as t:
            iset = os.path.join(t, "icon.iconset"); os.mkdir(iset)
            for s in (16, 32, 128, 256, 512):
                draw(MAC, s).save(os.path.join(iset, f"icon_{s}x{s}.png"))
                draw(MAC, s * 2).save(os.path.join(iset, f"icon_{s}x{s}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", iset, "-o", os.path.join(HERE, "icon.icns")], check=True)
