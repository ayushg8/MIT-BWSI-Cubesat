"""Rebuild every figure in docs/media/figures from the flight code and Images/.

    python docs/figures/make_figures.py [fig1 fig2 ...]

Needs requirements.txt plus matplotlib. Nothing here is hand-entered data: scores
come from QualityGate, the lock sweep from imaging._update_nadir_lock, the downlink
curve from running states/downlink.py against a stub socket on a virtual clock.
"""

# ruff: noqa: E501
import contextlib
import io
import os
import subprocess
import sys
import tempfile

import cv2
import matplotlib
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
from common import BLUE, INK, MUTED, ORANGE, PAPER, RULE  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.offsetbox import AnnotationBbox, OffsetImage  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
from protocol import ACK  # noqa: E402

for f in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
    if os.path.exists(f):
        font_manager.fontManager.addfont(f)

SANS = ["Arial", "Helvetica", "DejaVu Sans"]
MONO = ["Menlo", "DejaVu Sans Mono"]
plt.rcParams.update(
    {
        "figure.facecolor": PAPER,
        "axes.facecolor": PAPER,
        "savefig.facecolor": PAPER,
        "font.family": SANS,
        "font.size": 8.5,
        "text.color": INK,
        "axes.edgecolor": INK,
        "axes.labelcolor": INK,
        "axes.linewidth": 0.8,
        "xtick.color": INK,
        "ytick.color": INK,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.dpi": 200,
    }
)

WORK = tempfile.mkdtemp(prefix="figwork-")
PATHS = common.flight_jpegs(os.path.join(WORK, "flight"))
ROWS = common.score_all(PATHS)
FULL = common.fullres()
ID = lambda name: name[-10:-4]  # noqa: E731


def save(fig, name):
    path = os.path.join(common.OUT, name)
    fig.savefig(path)
    plt.close(fig)
    Image.open(path).save(path, optimize=True)
    print("wrote", path)


def caption(fig, title, note):
    fig.text(0.03, 0.985, title, ha="left", va="top", fontsize=10.5, fontweight="bold")
    fig.text(0.03, 0.01, note, ha="left", va="bottom", fontsize=7, color=MUTED)


def thumb(name, zoom):
    return OffsetImage(np.asarray(Image.open(PATHS[name]).convert("RGB")), zoom=zoom)


def fig_gate():
    names = sorted(ROWS, key=lambda n: ROWS[n]["variance"])
    fig = plt.figure(figsize=(8, 6.9))
    ax = fig.add_axes([0.2, 0.09, 0.74, 0.8])
    for i, n in enumerate(names):
        flight, big = ROWS[n]["variance"], FULL[n]
        ax.plot([flight, big], [i, i], color=RULE, lw=0.8, zorder=1)
        ax.plot(big, i, marker="D", ms=4, mfc=PAPER, mec=MUTED, mew=0.8, ls="", zorder=2)
        ok = ROWS[n]["status"] == "passed"
        ax.plot(flight, i, marker="o", ms=6, mfc=INK if ok else PAPER, mec=INK, mew=1, ls="", zorder=3)
        ax.text(
            max(flight, big) * 1.18,
            i,
            f"{flight:.2f}",
            va="center",
            fontsize=7.5,
            family=MONO,
            bbox={"fc": PAPER, "ec": "none", "pad": 0.8},
        )
        ab = AnnotationBbox(thumb(n, 0.045), (-0.16, i), xycoords=("axes fraction", "data"), frameon=False)
        ax.add_artist(ab)
        ax.text(
            -0.025, i, ID(n), transform=ax.get_yaxis_transform(), ha="right", va="center", fontsize=7.5, family=MONO
        )
    ax.axvline(config.BLUR_THRESHOLD, color=ORANGE, lw=1.4, zorder=0)
    ax.text(
        config.BLUR_THRESHOLD * 1.08,
        len(names) - 0.2,
        f"gate threshold {config.BLUR_THRESHOLD:.1f}",
        color=ORANGE,
        fontsize=8,
        va="bottom",
    )
    ax.set_xscale("log")
    ax.set_xlim(0.4, 900)
    ax.set_ylim(-0.7, len(names) - 0.3)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Laplacian variance of the grayscale frame (log scale)")
    ax.grid(axis="x", color=RULE, lw=0.5)
    ax.set_axisbelow(True)
    ax.text(0.45, 0.6, "REJECT", fontsize=7, color=MUTED, family=MONO)
    ax.text(40, 0.6, "ACCEPT", fontsize=7, color=MUTED, family=MONO)
    ax.plot([], [], "o", mfc=INK, mec=INK, label="640 x 480, accepted")
    ax.plot([], [], "o", mfc=PAPER, mec=INK, label="640 x 480, rejected")
    ax.plot([], [], "D", ms=4, mfc=PAPER, mec=MUTED, label="4608 x 2592, same photo")
    ax.legend(
        loc="lower right", bbox_to_anchor=(1.0, 0.04), frameon=False, fontsize=7.5, labelcolor=INK, handletextpad=0.4
    )
    n_ok = sum(r["status"] == "passed" for r in ROWS.values())
    caption(
        fig,
        f"Fig. 4: Blur variance per capture, {n_ok} of {len(names)} pass at 640 x 480",
        "Source: QualityGate.score_image on the 16 files in Images/ (gyro 0.0, novelty 1.0). Full-resolution values from docs/data/captures.json.",
    )
    save(fig, "fig-gate-variance.png")


def fig_contact():
    names = sorted(ROWS)
    fig = plt.figure(figsize=(8, 7.2))
    cols = 4
    for k, n in enumerate(names):
        r, c = divmod(k, cols)
        x0 = 0.04 + c * 0.24
        top = 0.93 - r * 0.222
        ax = fig.add_axes([x0, top - 0.1667, 0.2, 0.1667])
        ax.imshow(Image.open(PATHS[n]))
        ax.set_xticks([])
        ax.set_yticks([])
        ok = ROWS[n]["status"] == "passed"
        for s in ax.spines.values():
            s.set_visible(True)
            s.set_edgecolor(INK if ok else ORANGE)
            s.set_linewidth(0.8 if ok else 1.6)
        ax.text(
            0.03,
            0.05,
            "ACCEPT" if ok else "REJECT",
            transform=ax.transAxes,
            fontsize=7,
            family=MONO,
            color=INK if ok else ORANGE,
            bbox={"fc": PAPER, "ec": "none", "pad": 1.5},
        )
        fig.text(x0, top - 0.175, ID(n), fontsize=7.5, family=MONO, va="top")
        fig.text(x0 + 0.2, top - 0.175, f"{ROWS[n]['variance']:.2f}", fontsize=7.5, family=MONO, va="top", ha="right")
        fig.text(x0, top - 0.197, f"{ROWS[n]['bytes'] / 1000:.1f} kB", fontsize=7, family=MONO, va="top", color=MUTED)
    caption(
        fig,
        "Fig. 3: Contact sheet, every capture with its gate verdict",
        "Frames are the 640 x 480 re-encodes the gate scored. Label shows capture time, blur variance, JPEG size. Threshold 20.0. All 6 rejects are logged at the blur check, which runs first; 3 are near-black frames that would also fail exposure.",
    )
    save(fig, "fig-contact-sheet.png")


def fig_nadir():
    from states import imaging

    angles = list(range(90, -1, -1)) + list(range(1, 91))
    state, trace = False, []
    with contextlib.redirect_stdout(io.StringIO()):
        for a in angles:
            state = imaging._update_nadir_lock(state, float(a))
            trace.append(state)
    down = trace[:91]
    up = trace[91:]
    fig = plt.figure(figsize=(8, 3.5))
    ax = fig.add_axes([0.08, 0.22, 0.88, 0.66])
    ax.axvspan(config.NADIR_TOLERANCE_DEG, config.NADIR_EXIT_DEG, color=RULE, alpha=0.55, lw=0)
    ax.step(range(90, -1, -1), np.array(down) + 0.03, where="post", color=INK, lw=1.6)
    ax.step(range(1, 91), np.array(up) - 0.03, where="post", color=BLUE, lw=1.6)
    for x, lab, ha in (
        (config.NADIR_TOLERANCE_DEG, "engage below 45", "left"),
        (config.NADIR_EXIT_DEG, "release above 55", "right"),
    ):
        ax.axvline(x, color=ORANGE, lw=1.1)
        ax.text(x + (-1 if ha == "left" else 1), 1.4, lab, color=ORANGE, ha=ha, fontsize=8, family=MONO)
    ax.text(30, 1.1, "descending: locks at 44", color=INK, fontsize=8, ha="center", va="bottom")
    ax.text(30, 0.88, "returning: still locked until 56", color=BLUE, fontsize=8, ha="center", va="top")
    ax.set_xlim(90, 0)
    ax.set_ylim(-0.25, 1.5)
    ax.set_yticks([0, 1], ["unlocked", "locked"])
    ax.set_xlabel("angle from nadir (degrees)")
    ax.grid(axis="x", color=RULE, lw=0.5)
    ax.set_axisbelow(True)
    caption(
        fig,
        "Fig. 2: Nadir lock hysteresis, 10 degrees of dead band",
        "Source: 1 degree sweep 90 > 0 > 90 fed to imaging._update_nadir_lock (states/imaging.py). Black trace is the descent, blue is the return.",
    )
    save(fig, "fig-nadir-hysteresis.png")


class _Clock:
    def __init__(self):
        self.t = 0.0
        self.transfer = None
        self.points = [(0.0, 0)]

    def sleep(self, s):
        self.points.append((self.t, self.transfer.bytes_sent))
        self.t += s
        self.points.append((self.t, self.transfer.bytes_sent))

    def monotonic(self):
        return self.t


class _Sock:
    def sendall(self, _):
        pass

    def recv(self, _):
        return ACK

    def settimeout(self, _):
        pass

    def shutdown(self, _):
        pass

    def close(self):
        pass


class _Storage:
    def mark_sent(self, _):
        pass

    mark_failed = mark_sent


class _Dog:
    def pet(self):
        pass


def run_downlink():
    from comms import transfer as transfer_mod
    from states import downlink as downlink_mod

    clock = _Clock()
    transfer_mod.time = clock
    downlink_mod.time = clock

    class StubTransfer(transfer_mod.Transfer):
        def connect(self):
            self._sock = _Sock()

    tr = StubTransfer()
    clock.transfer = tr
    os.makedirs(config.IMAGE_DIR, exist_ok=True)
    ok = [n for n in ROWS if ROWS[n]["status"] == "passed"]
    queue = []
    for n in sorted(ok, key=lambda n: -ROWS[n]["score"]):
        with open(PATHS[n], "rb") as src, open(os.path.join(config.IMAGE_DIR, n), "wb") as dst:
            dst.write(src.read())
        queue.append({"filename": n, "file_size_bytes": ROWS[n]["bytes"]})
    with contextlib.redirect_stdout(io.StringIO()):
        result = downlink_mod.downlink(tr, _Storage(), queue, {"cubesat_id": config.CUBESAT_ID}, _Dog())
    return clock, queue, result


def fig_downlink():
    clock, queue, result = run_downlink()
    sent = result["images_sent"]
    pts = np.array(clock.points)
    budget = config.THROTTLE_BYTES_PER_SEC * config.DOWNLINK_WINDOW_SEC
    fig = plt.figure(figsize=(8, 4.6))
    ax = fig.add_axes([0.1, 0.2, 0.87, 0.7])
    edge, shade, t_prev = 0, False, 0.0
    for item in queue[:sent]:
        end = edge + item["file_size_bytes"]
        t0 = t_prev
        t1 = pts[np.searchsorted(pts[:, 1], end, side="left"), 0] + 1.0
        t_prev = t1
        ax.axvspan(t0, t1, color=RULE, alpha=0.5 if shade else 0.22, lw=0)
        ax.text((t0 + t1) / 2, budget * 1.095, ID(item["filename"]), ha="center", fontsize=7.5, family=MONO)
        ax.text(
            (t0 + t1) / 2,
            budget * 1.03,
            f"{item['file_size_bytes'] / 1000:.1f} kB",
            ha="center",
            fontsize=7,
            family=MONO,
            color=MUTED,
        )
        edge, shade = end, not shade
    ax.step(pts[:, 0], pts[:, 1], where="post", color=INK, lw=1.5)
    ax.axhline(budget, color=ORANGE, lw=1.2)
    ax.text(0.5, budget * 0.97, "72,000 byte budget", color=ORANGE, fontsize=8, va="top", family=MONO)
    ax.axvline(config.DOWNLINK_WINDOW_SEC, color=MUTED, lw=0.9, ls=(0, (4, 3)))
    ax.text(
        config.DOWNLINK_WINDOW_SEC - 0.6,
        budget * 0.04,
        "60 s window",
        color=MUTED,
        fontsize=7.5,
        ha="right",
        family=MONO,
    )
    stop = pts[-1]
    ax.plot(*stop, "o", ms=5, color=INK)
    nxt = queue[sent]
    ax.annotate(
        f"next: {ID(nxt['filename'])}, {nxt['file_size_bytes']:,} B\nwould pass the budget,\nloop stops. {len(queue) - sent} stay queued",
        xy=stop,
        xytext=(24, 55000),
        ha="left",
        va="bottom",
        fontsize=7.5,
        family=MONO,
        arrowprops={"arrowstyle": "-", "lw": 0.7, "color": MUTED},
    )
    ax.set_xlim(0, 64)
    ax.set_ylim(0, budget * 1.17)
    ax.set_yticks(range(0, 72001, 24000))
    ax.set_yticklabels([f"{v:,}" for v in range(0, 72001, 24000)], family=MONO, fontsize=7.5)
    ax.set_xlabel("seconds since downlink window opened")
    ax.set_ylabel("bytes delivered")
    ax.grid(color=RULE, lw=0.5)
    ax.set_axisbelow(True)
    caption(
        fig,
        "Fig. 5: One downlink window, highest score first",
        "Source: states/downlink.downlink() on a stub socket and a virtual clock (1,200 B chunks, 1 s sleep).\nQueue is the accepted frames, best score first; priority tiers need live coverage state, so they are not modelled.",
    )
    save(fig, "fig-downlink-timeline.png")
    print(
        f"downlink: {sent} sent, {result['bytes_sent']} B, remaining {len(result['remaining_queue'])}, t_end {clock.t:.0f}"
    )


def fig_offline():
    from processing import pixel_segmenter as ps
    from processing.mosaic_grid import MosaicGrid
    from processing.route_planner import RoutePlanner

    rng = np.random.default_rng(7)
    w, h = 480, 320
    scene = np.full((h, w), 190, np.uint8)
    scene = cv2.GaussianBlur((scene + rng.normal(0, 6, (h, w))).clip(0, 255).astype(np.uint8), (5, 5), 0)
    boulders = [(190, 60, 26, 20), (200, 120, 24, 22), (185, 215, 28, 20), (200, 275, 22, 24), (330, 150, 30, 24)]
    craters = [(110, 190, 34, 26), (380, 250, 30, 24)]
    dets = []
    for cls, items in (("boulder", boulders), ("crater", craters)):
        for cx, cy, ax_, ay in items:
            cv2.ellipse(scene, (cx, cy), (ax_, ay), 0, 0, 360, 60 if cls == "boulder" else 100, -1)
            dets.append(
                {"class": cls, "bbox": [cx - ax_ - 4, cy - ay - 4, cx + ax_ + 4, cy + ay + 4], "confidence": 1.0}
            )
    shadow = np.zeros((h, w), np.uint8)
    for cx, cy, ax_, ay in boulders:
        cv2.ellipse(shadow, (cx + 22, cy + 6), (ax_, ay // 2), 0, 0, 360, 255, -1)
    img_path = os.path.join(WORK, "synthetic_scene.png")
    cv2.imwrite(img_path, scene)

    labels = ps.PixelSegmenter().segment(img_path, shadow, dets)
    grid = MosaicGrid()
    grid.update_from_mosaic(w, h)
    grid.apply_segmentation_mask((0, 0, w, h), labels)
    route = RoutePlanner().plan_route(grid, (20, 160), (460, 160))
    assert route["success"]
    cost = grid.get_fine_cost_grid()
    cell = config.SEG_GRID_CELL_PX

    pal = {
        ps.SAND: "#E4DFD2",
        ps.PLAIN_SURFACE: "#E4DFD2",
        ps.SHADOW: "#A9A598",
        ps.CRATER: BLUE,
        ps.BOULDER: INK,
        ps.UNSURVEYED: PAPER,
    }
    seg = np.zeros((h, w, 3), np.uint8)
    for lab, col in pal.items():
        seg[labels == lab] = [int(col[i : i + 2], 16) for i in (1, 3, 5)]

    fig, axes = plt.subplots(1, 3, figsize=(8, 3.0))
    fig.subplots_adjust(left=0.02, right=0.98, top=0.78, bottom=0.26, wspace=0.06)
    axes[0].imshow(scene, cmap="gray", vmin=0, vmax=255)
    axes[1].imshow(seg)
    axes[2].imshow(
        np.log10(cost),
        cmap=matplotlib.colors.LinearSegmentedColormap.from_list("c", [PAPER, RULE, MUTED, INK]),
        extent=(0, cost.shape[1] * cell, cost.shape[0] * cell, 0),
        interpolation="nearest",
    )
    px = [p[0] for p in route["path_mosaic"]]
    py = [p[1] for p in route["path_mosaic"]]
    axes[2].plot(px, py, color=ORANGE, lw=2)
    axes[2].plot([px[0], px[-1]], [py[0], py[-1]], "o", ms=4, color=ORANGE)
    titles = ["synthetic scene", "PixelSegmenter label map", "fine cost grid + A* route"]
    for a, t in zip(axes, titles):
        a.set_xticks([])
        a.set_yticks([])
        for s in a.spines.values():
            s.set_visible(True)
            s.set_linewidth(0.8)
        a.set_title(t, fontsize=8, loc="left", pad=4)
    for i, (lab, col) in enumerate((("sand", "#E4DFD2"), ("shadow", "#A9A598"), ("crater", BLUE), ("boulder", INK))):
        fig.patches.append(
            plt.Rectangle((0.355 + i * 0.08, 0.14), 0.012, 0.03, transform=fig.transFigure, fc=col, ec=INK, lw=0.5)
        )
        fig.text(0.372 + i * 0.08, 0.155, lab, fontsize=7, va="center")
    fig.text(
        0.665,
        0.155,
        f"route cost {route['cost']:.0f}, {len(route['path_grid'])} cells",
        fontsize=7,
        va="center",
        family=MONO,
        color=ORANGE,
    )
    caption(
        fig,
        "Fig. 6: Offline analysis layer, not in the flight loop",
        "Synthetic scene (drawn ellipses, hand-placed detection boxes) run through PixelSegmenter, MosaicGrid and RoutePlanner. Not satellite imagery.",
    )
    save(fig, "fig-offline-analysis.png")


# Terminal GIF --------------------------------------------------------------
def run_cmd(cmd, cwd):
    env = dict(os.environ, CUBESAT_DATA_ROOT=tempfile.mkdtemp(prefix="muraltz-t-"))
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return (p.stdout + p.stderr).rstrip("\n").splitlines()


def terminal_gif():
    from PIL import ImageDraw, ImageFont

    py = sys.executable
    sessions = [
        ("python docs/figures/gate_report.py", [py, os.path.join(HERE, "gate_report.py")], common.ROOT),
        (
            "cd cubesat_flight && python -m unittest -v test_processing.py",
            [py, "-m", "unittest", "-v", "test_processing.py"],
            common.FLIGHT,
        ),
    ]
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    W, H, pad, lh = 1000, 560, 22, 18
    rgb = lambda c: tuple(int(c[i : i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    lines, frames = [], []

    def render():
        im = Image.new("RGB", (W, H), rgb(PAPER))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, W, 30], fill=rgb(RULE))
        d.text(
            (pad, 7),
            "muraltz-cubesat -- zsh",
            font=ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 12),
            fill=rgb(MUTED),
        )
        visible = lines[-(H - 42 - pad) // lh :]
        for i, (txt, col) in enumerate(visible):
            d.text((pad, 42 + i * lh), txt, font=font, fill=rgb(col))
        frames.append(im)

    def colour(t):
        return (
            ORANGE
            if "REJECT" in t or "FAIL" in t or "ERROR" in t
            else (MUTED if t.startswith(("file", "---", "Ran")) else INK)
        )

    for shown, cmd, cwd in sessions:
        lines.append(("$ ", INK))
        for k in range(0, len(shown) + 1, 4):
            lines[-1] = ("$ " + shown[:k] + "_", INK)
            render()
        lines[-1] = ("$ " + shown, INK)
        render()
        out = run_cmd(cmd, cwd)
        for j, t in enumerate(out):
            lines.append((t, colour(t)))
            if j % 2 == 1 or j == len(out) - 1:
                render()
        lines.append(("", INK))
    lines.append(("$ _", INK))
    render()

    fdir = os.path.join(WORK, "term")
    os.makedirs(fdir, exist_ok=True)
    for i, im in enumerate(frames):
        im.save(os.path.join(fdir, f"f{i:03d}.png"))
    last = len(frames) - 1
    # per-frame hold: typing fast, output steady, long hold on the end
    with open(os.path.join(fdir, "list.txt"), "w") as f:
        for i in range(len(frames)):
            f.write(f"file 'f{i:03d}.png'\nduration {2.5 if i == last else (0.07 if i < len(frames) else 0.07)}\n")
        f.write(f"file 'f{last:03d}.png'\n")
    out_gif = os.path.join(common.OUT, "terminal-gate-and-tests.gif")
    pal = os.path.join(fdir, "p.png")
    base = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(fdir, "list.txt")]
    subprocess.run(base + ["-vf", "palettegen=max_colors=24", pal], check=True)
    subprocess.run(base + ["-i", pal, "-lavfi", "paletteuse=dither=none", out_gif], check=True)
    print("wrote", out_gif, os.path.getsize(out_gif) // 1024, "KB")


# Animated state machine SVG -------------------------------------------------
def state_svg():
    states = ["BOOT", "WAITING", "IMAGING", "IDLE", "DOWNLINK"]
    notes = ["self-test", "start_pass", "IMU gated", "build queue", "1,200 B/s"]
    total = 1.6 * len(states)
    bw, gap, x0, y0 = 128, 36, 30, 70
    width = x0 * 2 + bw * 5 + gap * 4
    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 280" width="{width}" height="280" font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="280" fill="{PAPER}"/>',
        f'<text x="{x0}" y="30" font-size="15" font-weight="700" fill="{INK}">Fig. 1: Flight state machine, one pass</text>',
        f'<text x="{x0}" y="48" font-size="11" fill="{MUTED}">Active state steps in order; dwell times are not to scale. Dashed: SAFE_MODE, entered on a failed self-test (also on CPU above 80 C, not drawn).</text>',
    ]
    for i, (s, n) in enumerate(zip(states, notes)):
        x = x0 + i * (bw + gap)
        on = f'<animate attributeName="fill" values="{PAPER};{PAPER};{INK};{INK};{PAPER};{PAPER}" keyTimes="0;{max(i / 5 - 0.001, 0):.4f};{i / 5:.4f};{(i + 1) / 5 - 0.001:.4f};{(i + 1) / 5:.4f};1" dur="{total}s" repeatCount="indefinite"/>'
        txt = f'<animate attributeName="fill" values="{INK};{INK};{PAPER};{PAPER};{INK};{INK}" keyTimes="0;{max(i / 5 - 0.001, 0):.4f};{i / 5:.4f};{(i + 1) / 5 - 0.001:.4f};{(i + 1) / 5:.4f};1" dur="{total}s" repeatCount="indefinite"/>'
        note = txt.replace(INK, MUTED).replace(PAPER, RULE)
        o.append(
            f'<rect x="{x}" y="{y0 + 30}" width="{bw}" height="64" fill="{PAPER}" stroke="{INK}" stroke-width="1.2">{on}</rect>'
        )
        o.append(
            f'<text x="{x + bw / 2}" y="{y0 + 59}" text-anchor="middle" font-size="14" font-weight="700" fill="{INK}">{s}{txt}</text>'
        )
        o.append(
            f'<text x="{x + bw / 2}" y="{y0 + 80}" text-anchor="middle" font-size="11" font-family="Menlo, Consolas, monospace" fill="{MUTED}">{n}{note}</text>'
        )
        if i < 4:
            ax = x + bw
            o.append(
                f'<line x1="{ax}" y1="{y0 + 62}" x2="{ax + gap - 6}" y2="{y0 + 62}" stroke="{INK}" stroke-width="1.2"/>'
            )
            o.append(
                f'<path d="M{ax + gap - 6} {y0 + 57} L{ax + gap} {y0 + 62} L{ax + gap - 6} {y0 + 67}" fill="none" stroke="{INK}" stroke-width="1.2"/>'
            )
    # DOWNLINK back to WAITING
    xa = x0 + 4 * (bw + gap) + bw / 2
    xb = x0 + 1 * (bw + gap) + bw / 2
    yb = y0 + 94
    o.append(f'<path d="M{xa} {yb} V{yb + 36} H{xb} V{yb + 2}" fill="none" stroke="{ORANGE}" stroke-width="1.4"/>')
    o.append(
        f'<path d="M{xb - 5} {yb + 8} L{xb} {yb} L{xb + 5} {yb + 8}" fill="none" stroke="{ORANGE}" stroke-width="1.4"/>'
    )
    o.append(
        f'<text x="{(xa + xb) / 2}" y="{yb + 52}" text-anchor="middle" font-size="11" font-family="Menlo, Consolas, monospace" fill="{ORANGE}">window over, back to WAITING</text>'
    )
    sy = y0 + 142
    mono = f'font-family="Menlo, Consolas, monospace" font-size="11" fill="{MUTED}"'
    dash = f'fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5 4"'
    o.append(f'<rect x="{x0}" y="{sy}" width="{bw}" height="44" {dash}/>')
    o.append(
        f'<text x="{x0 + bw / 2}" y="{sy + 27}" text-anchor="middle" font-size="13" font-weight="700" fill="{MUTED}">SAFE_MODE</text>'
    )
    o.append(f'<path d="M{x0 + bw / 2} {y0 + 94} V{sy - 1}" {dash}/>')
    o.append(
        f'<path d="M{x0 + bw / 2 - 4} {sy - 7} L{x0 + bw / 2} {sy - 1} L{x0 + bw / 2 + 4} {sy - 7}" fill="none" stroke="{MUTED}" stroke-width="1.2"/>'
    )
    o.append(f'<text x="{x0 + bw / 2 + 8}" y="{y0 + 124}" {mono}>self-test fail</text>')
    xr = xb - 40
    o.append(f'<path d="M{x0 + bw} {sy + 22} H{xr} V{y0 + 95}" {dash}/>')
    o.append(
        f'<path d="M{xr - 4} {y0 + 101} L{xr} {y0 + 95} L{xr + 4} {y0 + 101}" fill="none" stroke="{MUTED}" stroke-width="1.2"/>'
    )
    o.append(f'<text x="{x0 + bw + 8}" y="{sy + 40}" {mono}>resume_normal</text>')
    o.append("</svg>")
    path = os.path.join(common.OUT, "state-machine.svg")
    with open(path, "w") as f:
        f.write("\n".join(o))
    print("wrote", path)


ALL = {
    "gate": fig_gate,
    "contact": fig_contact,
    "nadir": fig_nadir,
    "downlink": fig_downlink,
    "offline": fig_offline,
    "terminal": terminal_gif,
    "svg": state_svg,
}

if __name__ == "__main__":
    os.makedirs(common.OUT, exist_ok=True)
    for key in sys.argv[1:] or ALL:
        ALL[key]()
