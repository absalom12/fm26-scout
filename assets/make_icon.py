"""Draw the app icon (quartered shield + magnifier) and write icon.png / icon.ico / icon.icns. Run: python assets/make_icon.py"""
import os, subprocess, sys, tempfile
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S, K = 1024, 4  # final size, supersampling factor


def bez(p0, p1, p2, p3, n=40):
    return [tuple((1-t)**3*a + 3*(1-t)**2*t*b + 3*(1-t)*t**2*c + t**3*d for a, b, c, d in zip(p0, p1, p2, p3))
            for t in [i / n for i in range(n + 1)]]


def shield(cx, cy, w, h):
    """Shield from the page logo (viewBox 26x30), scaled to w x h centred on (cx, cy)."""
    sx, sy = w / 24, h / 28
    P = lambda x, y: (cx + (x - 13) * sx, cy + (y - 15) * sy)
    pts = [P(13, 1), P(25, 5), P(25, 15)]
    pts += [P(*q) for q in bez((25, 15), (25, 22), (20, 27), (13, 29))]
    pts += [P(*q) for q in bez((13, 29), (6, 27), (1, 22), (1, 15))]
    pts += [P(1, 5)]
    return pts, P


def build():
    n = S * K
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(n * 0.06)  # macOS icon grid margin
    # background: rounded square, vertical dark-blue gradient
    bg = Image.new("RGBA", (n, n))
    bd = ImageDraw.Draw(bg)
    for y in range(n):
        t = y / n
        bd.line([(0, y), (n, y)], fill=(int(17 + 9 * t), int(27 + 12 * t), int(40 + 20 * t), 255))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, n - m, n - m], radius=int(n * 0.2), fill=255)
    img.paste(bg, (0, 0), mask)
    # shield
    big, _ = shield(n * 0.47, n * 0.47, n * 0.54, n * 0.62)  # white rim = slightly larger shield behind
    d.polygon(big, fill=(232, 238, 243, 255))
    pts, P = shield(n * 0.47, n * 0.47, n * 0.50, n * 0.58)
    d.polygon(pts, fill=(74, 163, 255, 255))
    quarter = Image.new("L", (n, n), 0)
    qd = ImageDraw.Draw(quarter)
    qd.polygon(pts, fill=255)
    cut = Image.new("L", (n, n), 0)
    cx, cy = P(13, 15)
    ImageDraw.Draw(cut).rectangle([0, cy, cx, n], fill=255)  # bottom-left quarter, dark like the page logo
    from PIL import ImageChops
    dark = ImageChops.multiply(quarter, cut)
    img.paste(Image.new("RGBA", (n, n), (14, 22, 34, 255)), (0, 0), dark)
    # magnifier: white ring + handle, bottom-right, over the shield
    lx, ly, r = n * 0.60, n * 0.60, n * 0.13
    d.line([(lx + r * 0.7, ly + r * 0.7), (n * 0.80, n * 0.80)], fill=(232, 238, 243, 255), width=int(n * 0.05))
    d.ellipse([lx - r, ly - r, lx + r, ly + r], fill=(24, 44, 70, 255), outline=(232, 238, 243, 255), width=int(n * 0.03))
    d.arc([lx - r * 0.6, ly - r * 0.6, lx + r * 0.6, ly + r * 0.6], 200, 260, fill=(120, 190, 255, 255), width=int(n * 0.015))  # glass glint
    return img.resize((S, S), Image.LANCZOS)


if __name__ == "__main__":
    img = build()
    img.save(os.path.join(HERE, "icon.png"))
    img.save(os.path.join(HERE, "icon.ico"), sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as t:
            iset = os.path.join(t, "icon.iconset"); os.mkdir(iset)
            for s in (16, 32, 128, 256, 512):
                img.resize((s, s), Image.LANCZOS).save(os.path.join(iset, f"icon_{s}x{s}.png"))
                img.resize((s * 2, s * 2), Image.LANCZOS).save(os.path.join(iset, f"icon_{s}x{s}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", iset, "-o", os.path.join(HERE, "icon.icns")], check=True)
