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
    # footballer mid-kick (blue, white rim), ball, magnifier. Coordinates are a 0-100 grid.
    u = n / 100
    X = lambda x, y: (x * u, y * u)
    BLUE, RIM, DARK = (74, 163, 255, 255), (232, 238, 243, 255), (11, 15, 19, 255)

    def limb(pts, w, col):
        d.line([X(*q) for q in pts], fill=col, width=int(w * u), joint="curve")
        for q in (pts[0], pts[-1]):
            x, y = X(*q); r = w * u / 2
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)

    def figure(col, grow):
        g = grow
        limb([(51, 30), (48, 52)], 10 + g, col)               # torso
        limb([(50, 34), (40, 42), (33, 33)], 5 + g, col)      # back arm
        limb([(52, 34), (62, 39), (68, 47)], 5 + g, col)      # front arm
        limb([(48, 52), (46, 68), (42, 85)], 7 + g, col)      # standing leg
        limb([(48, 52), (60, 62), (73, 68)], 7 + g, col)      # kicking leg
        x, y = X(52, 18); r = (7.5 + g / 2) * u
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)     # head
        x, y = X(83, 78); r = (6 + g / 2) * u
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)     # ball

    figure(RIM, 3.5)   # white rim first, then the blue figure on top
    figure(BLUE, 0)
    x, y = X(83, 78); r = 6 * u
    d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255))
    import math
    cx, cy = X(83, 78)
    d.polygon([(cx + 2.6 * u * math.sin(a * math.pi / 2.5), cy - 2.6 * u * math.cos(a * math.pi / 2.5)) for a in range(5)], fill=DARK)  # ball patch
    # magnifier, bottom-left
    lx, ly, r = X(26, 70)[0], X(26, 70)[1], 11 * u
    d.line([(lx - r * 0.7, ly + r * 0.7), X(11.5, 84.5)], fill=RIM, width=int(4.6 * u))
    d.ellipse([X(11.5, 84.5)[0] - 2.3 * u, X(11.5, 84.5)[1] - 2.3 * u, X(11.5, 84.5)[0] + 2.3 * u, X(11.5, 84.5)[1] + 2.3 * u], fill=RIM)
    d.ellipse([lx - r, ly - r, lx + r, ly + r], fill=(24, 44, 70, 255), outline=RIM, width=int(2.8 * u))
    d.arc([lx - r * 0.6, ly - r * 0.6, lx + r * 0.6, ly + r * 0.6], 200, 260, fill=(120, 190, 255, 255), width=int(1.4 * u))
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
