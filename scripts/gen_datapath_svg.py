"""Generate docs/datapath.svg -- the tt_um_sky26c datapath block diagram.

Run from the repo root:  python3 scripts/gen_datapath_svg.py

The SVG is committed; this exists so the layout is reproducible rather than
hand-tweaked. figures/datapath.tex and docs/datapath.mmd carry the same
topology for LaTeX and Obsidian -- update all three together.
"""

from pathlib import Path

W, H = 1300, 640

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#52514e"
SYN = "#2a78d6"   # synapse lanes, matches SERIES_I in the test plots
MEM = "#c2570f"   # membrane lane, matches SERIES_V
TINT_SYN = "#eef4fc"
TINT_MEM = "#fdf1e8"

BW, BH = 140, 46          # block size
COLS = [200, 360, 520, 680, 840, 1000]
LANE = {"syn": 100, "cur": 260, "mem": 420}   # lane centre y

out = []


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def block(col, lane, title, sub="", color=SYN, reg=False, w=BW):
    x, cy = COLS[col], LANE[lane]
    y = cy - BH / 2
    fill = (TINT_SYN if color == SYN else TINT_MEM) if reg else "#ffffff"
    sw = 2.0 if reg else 1.3
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{BH}" rx="4" '
        f'fill="{fill}" stroke="{color}" stroke-width="{sw}"/>'
    )
    if reg:  # clock notch, bottom-left
        out.append(
            f'<path d="M{x + 6},{y + BH} l7,-7 l7,7" fill="none" '
            f'stroke="{color}" stroke-width="1.3"/>'
        )
    ty = cy + (-3 if sub else 4)
    out.append(
        f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" font-size="12.5" '
        f'font-weight="600" fill="{INK}">{esc(title)}</text>'
    )
    if sub:
        out.append(
            f'<text x="{x + w / 2}" y="{cy + 12}" text-anchor="middle" '
            f'font-size="10" fill="{MUTED}">{esc(sub)}</text>'
        )
    return x, cy


def arrow(pts, color=MUTED, label="", dash=False, lx=None, ly=None):
    d = " ".join(f"{x},{y}" for x, y in pts)
    da = ' stroke-dasharray="4 3"' if dash else ""
    mk = "aS" if color == SYN else ("aM" if color == MEM else "aG")
    out.append(
        f'<polyline points="{d}" fill="none" stroke="{color}" '
        f'stroke-width="1.4"{da} marker-end="url(#{mk})"/>'
    )
    if label:
        if lx is None:
            (x0, y0), (x1, y1) = pts[0], pts[1]
            lx, ly = (x0 + x1) / 2, min(y0, y1) - BH / 2 - 8
        out.append(
            f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="9.5" '
            f'font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
            f'fill="{MUTED}">{esc(label)}</text>'
        )


def lane_label(lane, text, color, desc="", pin="", pin_desc=""):
    y = LANE[lane]
    if pin:
        out.append(
            f'<text x="20" y="{y - 46}" font-size="10.5" font-weight="700" '
            f'fill="{INK}">{esc(pin)}</text>'
        )
        out.append(
            f'<text x="20" y="{y - 34}" font-size="9.5" fill="{MUTED}">{esc(pin_desc)}</text>'
        )
    out.append(
        f'<text x="20" y="{y - 18}" font-size="10.5" font-weight="700" '
        f'letter-spacing="0.6" fill="{color}">{esc(text)}</text>'
    )
    if desc:
        out.append(f'<text x="20" y="{y - 6}" font-size="9.5" fill="{MUTED}">{esc(desc)}</text>')


def pin_out(lane, name, fmt):
    x = 1205
    out.append(
        f'<text x="{x}" y="{LANE[lane] - 10}" text-anchor="middle" font-size="11" '
        f'font-weight="600" fill="{INK}">{esc(name)}</text>'
    )
    out.append(
        f'<text x="{x}" y="{LANE[lane] + 18}" text-anchor="middle" font-size="9.5" '
        f'fill="{MUTED}">{esc(fmt)}</text>'
    )


# ---------------------------------------------------------------- lane 1: r
lane_label("syn", "SYNAPTIC KINETICS", SYN, "exact dr/dt = α(1−r) − βr",
           "ui_in[7]", "pre-synaptic spike")
arrow([(150, LANE["syn"]), (COLS[0], LANE["syn"])], MUTED)

block(0, "syn", "sel e^−rate", "spike ? e^−(α+β) : e^−β", SYN)
block(1, "syn", "× 16×16", "r × mat_exp", SYN)
block(2, "syn", "round ≫16", "Q0.32 → Q0.16", SYN)
block(3, "syn", "+ B_SPK", "spike branch only", SYN)
block(4, "syn", "saturate", "clamp at Q0.16 max", SYN)
block(5, "syn", "REG  r", "Q0.16 open fraction", SYN, reg=True)

for c in range(5):
    arrow([(COLS[c] + BW, LANE["syn"]), (COLS[c + 1], LANE["syn"])], SYN)

# r feedback, routed above the lane
arrow(
    [(COLS[5] + BW / 2, LANE["syn"] - BH / 2), (COLS[5] + BW / 2, 46),
     (COLS[1] + BW / 2, 46), (COLS[1] + BW / 2, LANE["syn"] - BH / 2)],
    SYN, label="r  (Q0.16)", lx=(COLS[1] + COLS[5]) / 2 + BW / 2, ly=41,
)

# ------------------------------------------------- lane 2: conductance, I_syn
lane_label("cur", "CONDUCTANCE & CURRENT", SYN, "g = g_max·r")

block(0, "cur", "× g_max", "g_max = 255", SYN)
block(1, "cur", "round ≫8", "→ g  Q0.16", SYN)
block(2, "cur", "× signed", "g × driving force", SYN)
block(3, "cur", "round ≫16", "Q0.24 → Q0.8 signed", SYN)
block(4, "cur", "saturate", "negative → 0", SYN)

for c in range(4):
    arrow([(COLS[c] + BW, LANE["cur"]), (COLS[c + 1], LANE["cur"])], SYN)

# r_next tapped out of the saturate stage, down into the conductance lane
arrow(
    [(COLS[4] + BW / 2, LANE["syn"] + BH / 2), (COLS[4] + BW / 2, 178),
     (95, 178), (95, LANE["cur"]), (COLS[0], LANE["cur"])],
    SYN, label="r_next", lx=480, ly=173,
)

# I_syn out to the pin
arrow([(COLS[4] + BW, LANE["cur"]), (1270, LANE["cur"])], SYN)
pin_out("cur", "uo_out[7:0]", "I_syn  Q0.8")

# --------------------------------------------------------- lane 3: membrane
lane_label("mem", "MEMBRANE & SPIKE", MEM, "integrate, fire, reset",
           "ui_in[6]", "voltage clamp")
arrow([(150, LANE["mem"]), (COLS[0], LANE["mem"])], MUTED)

block(0, "mem", "E_rev − V", "clamp ? +256 : signed", MEM)
block(1, "mem", "× LEAK", "→ I_leak  Q0.8", MEM)
block(2, "mem", "V + I_syn − I_leak", "13-bit signed", MEM)
block(3, "mem", "saturate", "0 … 255", MEM)
block(4, "mem", "> V_threshold", "192  (Q0.8)", MEM)
block(5, "mem", "REG  V, spike", "fire & reset", MEM, reg=True)

arrow([(COLS[1] + BW, LANE["mem"]), (COLS[2], LANE["mem"])], MEM, label="I_leak")
for c in (2, 3, 4):
    arrow([(COLS[c] + BW, LANE["mem"]), (COLS[c + 1], LANE["mem"])], MEM)

# driving force up into the current lane
arrow(
    [(COLS[0] + BW / 2, LANE["mem"] - BH / 2), (COLS[0] + BW / 2, 330),
     (COLS[2] + 60, 330), (COLS[2] + 60, LANE["cur"] + BH / 2)],
    MEM, label="E_rev − V  (signed Q0.8)", lx=400, ly=325,
)

# I_syn down into the membrane sum
arrow(
    [(COLS[3] + BW / 2, LANE["cur"] + BH / 2), (COLS[3] + BW / 2, 360),
     (COLS[2] + 90, 360), (COLS[2] + 90, LANE["mem"] - BH / 2)],
    SYN, label="I_syn", lx=700, ly=355,
)

# V feedback, routed below the lane
arrow(
    [(COLS[5] + BW / 2, LANE["mem"] + BH / 2), (COLS[5] + BW / 2, 505),
     (COLS[0] + BW / 2, 505), (COLS[0] + BW / 2, LANE["mem"] + BH / 2)],
    MEM, label="V  (Q0.8)", lx=(COLS[0] + COLS[5]) / 2 + BW / 2, ly=518,
)
# V also feeds the leak multiply
arrow([(COLS[1] + BW / 2, 505), (COLS[1] + BW / 2, LANE["mem"] + BH / 2)], MEM)
out.append(
    f'<circle cx="{COLS[1] + BW / 2}" cy="505" r="3" fill="{MEM}"/>'
)

# spike / V out to the bidir pins
arrow([(COLS[5] + BW, LANE["mem"]), (1270, LANE["mem"])], MEM)
pin_out("mem", "uio_out[7:0]", "{V[7:1], spike}")


# ------------------------------------------------------------------ chrome
head = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
    f'height="{H}" font-family="Inter,Helvetica Neue,Arial,sans-serif">',
    "<defs>",
]
for mid, col in (("aS", SYN), ("aM", MEM), ("aG", MUTED)):
    head.append(
        f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,1 L9,5 L0,9 z" fill="{col}"/></marker>'
    )
head.append("</defs>")
head.append(f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>')
head.append(
    f'<text x="20" y="30" font-size="16" font-weight="700" fill="{INK}">'
    f'tt_um_sky26c — first-order kinetic synapse driving a conductance-based LIF membrane</text>'
)

foot = [
    f'<text x="20" y="{H - 34}" font-size="10" fill="{MUTED}">'
    f'All rescales are power-of-two slices with round-to-nearest; every stage saturates rather than wraps. '
    f'Coefficients are compile-time constants (shift-in registers once the load path exists).</text>',
    f'<text x="20" y="{H - 18}" font-size="10" fill="{MUTED}">'
    f'α = 25/256, β = 56/256, r_inf = 25/81 · g_max = 255 · E_rev = 255 · LEAK = 16 · V_threshold = 192 · '
    f'Under voltage clamp V is held and the driving force is unity, so uo_out reports g directly.</text>',
    "</svg>",
]

Path("docs/datapath.svg").write_text("\n".join(head + out + foot) + "\n")
print("wrote docs/datapath.svg")
