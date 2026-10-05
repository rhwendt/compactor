"""Render compactor's icon (plugins/compactor/.claude-plugin/icon.png): a pause symbol between
inward chevrons, 1024x1024 PNG. Stdlib only. Usage: python3 scripts/make_icon.py <out.png>"""
import math, struct, sys, zlib

N = 1024

def sd_round_rect(px, py, cx, cy, hw, hh, r):
    qx, qy = abs(px - cx) - (hw - r), abs(py - cy) - (hh - r)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r

def sd_capsule(px, py, ax, ay, bx, by, r):
    pax, pay, bax, bay = px - ax, py - ay, bx - ax, by - ay
    h = max(0.0, min(1.0, (pax * bax + pay * bay) / (bax * bax + bay * bay)))
    return math.hypot(pax - bax * h, pay - bay * h) - r

def cov(d):  # anti-aliased coverage from a signed distance in pixels
    return max(0.0, min(1.0, 0.5 - d))

top, bot = (79, 70, 229), (124, 58, 237)  # indigo-600 -> violet-600
c = N / 2
bar_hw, bar_hh, gap = 58, 220, 54
chev_r, chev_dx, chev_dy = 28, 78, 140
lx, rx = 158, N - 158  # chevron tips at lx + chev_dx, clear of the bars

rows = []
for y in range(N):
    py = y + 0.5
    t = y / (N - 1)
    bg = tuple(top[i] + (bot[i] - top[i]) * t for i in range(3))
    row = bytearray(b"\x00")
    for x in range(N):
        px = x + 0.5
        a_bg = cov(sd_round_rect(px, py, c, c, N / 2, N / 2, 224))
        if a_bg == 0:
            row += b"\x00\x00\x00\x00"; continue
        d_bars = min(sd_round_rect(px, py, c - gap - bar_hw, c, bar_hw, bar_hh, 40),
                     sd_round_rect(px, py, c + gap + bar_hw, c, bar_hw, bar_hh, 40))
        d_chev = min(
            sd_capsule(px, py, lx, c - chev_dy, lx + chev_dx, c, chev_r),
            sd_capsule(px, py, lx, c + chev_dy, lx + chev_dx, c, chev_r),
            sd_capsule(px, py, rx, c - chev_dy, rx - chev_dx, c, chev_r),
            sd_capsule(px, py, rx, c + chev_dy, rx - chev_dx, c, chev_r))
        white = max(cov(d_bars), 0.72 * cov(d_chev))
        rgb = [bg[i] * (1 - white) + 255 * white for i in range(3)]
        row += bytes([int(round(v)) for v in rgb] + [int(round(255 * a_bg))])
    rows.append(bytes(row))

def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0)) \
      + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
open(sys.argv[1], "wb").write(png)
print(len(png), "bytes")
