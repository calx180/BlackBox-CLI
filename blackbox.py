#!/usr/bin/env python3
"""
blackbox.py -- a terminal screensaver inspired by the little floating
drone companions in NieR: Automata. A cube tumbles slowly in place,
its faces etched with stepped circuit-trace bands (matching the
reference toy's artwork) and its structural edges rendered with
Unicode braille sub-cell dots for fine, near-vector line detail,
orbited by drifting scan particles, with an occasional flicker of
block-glyph static and a HUD-style status readout at the bottom.

Run two copies in separate windows under Hyprland and drag them near
each other: each instance polls `hyprctl` for other Blackbox windows
and, once they're close enough on screen, both burst into a bright
white flash at once -- no direct connection between the two processes,
just the same shared ground truth from the compositor. Press 'b' at
any time to trigger the burst manually and preview it.

Controls: q or ESC to quit, b to trigger a manual burst. Ctrl+C also
works.

Requires a UTF-8 locale and a terminal font with braille glyph
coverage (nearly all modern terminal fonts have this). The
cross-window burst additionally requires Hyprland (uses `hyprctl`) --
on anything else it simply stays inactive and only the manual 'b' key
still works. No external dependencies -- just Python's stdlib.
"""

import argparse
import curses
import json
import locale
import math
import random
import shutil
import subprocess
import sys
import time
import uuid

# ---------------------------------------------------------------- geometry

# Unit cube corners
CUBE_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1),  (1, -1, 1),  (1, 1, 1),  (-1, 1, 1),
]

CUBE_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),  # back face
    (4, 5), (5, 6), (6, 7), (7, 4),  # front face
    (0, 4), (1, 5), (2, 6), (3, 7),  # connectors
]

# (axis, sign) for each of the 6 cube faces
FACE_SIGNS = [(0, 1), (0, -1), (1, 1), (1, -1), (2, 1), (2, -1)]

# horizontal trace bands per face (v-center positions), and how far a
# step rises/falls from its band's center line
BAND_V_LEVELS = [-0.55, -0.05, 0.45]
BAND_STEP_AMP = 0.14
BAND_U_RANGE = (-0.94, 0.94)

STATUS_MESSAGES = [
    "STATUS: NOMINAL",
    "SCANNING SECTOR",
    "MEMORY SYNC 87%",
    "AWAITING INPUT",
    "NO SIGNAL",
    "CALIBRATING",
    "IDLE",
    "LINK STABLE",
]

GLITCH_GLYPHS = "\u2591\u2592\u2593\u2588\u2596\u2597\u2598\u259d"  # ░▒▓█▖▗▘▝
NODE_GLYPHS = ("\u25cb", "\u25c7")  # ○ ◇ -- thin outline marks, like the reference's inline components

# Unicode braille dot bit weights by (local_row 0-3, local_col 0-1) within a cell
_BRAILLE_BIT = {
    (0, 0): 0x01, (1, 0): 0x02, (2, 0): 0x04, (3, 0): 0x40,
    (0, 1): 0x08, (1, 1): 0x10, (2, 1): 0x20, (3, 1): 0x80,
}
_BRAILLE_BASE = 0x2800


def rotate(x, y, z, ax, ay, az):
    cosx, sinx = math.cos(ax), math.sin(ax)
    y, z = y * cosx - z * sinx, y * sinx + z * cosx
    cosy, siny = math.cos(ay), math.sin(ay)
    x, z = x * cosy + z * siny, -x * siny + z * cosy
    cosz, sinz = math.cos(az), math.sin(az)
    x, y = x * cosz - y * sinz, x * sinz + y * cosz
    return x, y, z


def face_point(axis, sign, a, b):
    """Map a 2D point (a,b in [-1,1]) on one cube face into 3D space."""
    p = [0.0, 0.0, 0.0]
    other_axes = [ax for ax in (0, 1, 2) if ax != axis]
    p[axis] = sign * 1.0
    p[other_axes[0]] = a
    p[other_axes[1]] = b
    return tuple(p)


def build_face_traces():
    """Horizontal stepped circuit-trace bands etched into each cube
    face, matching the reference Blackbox artwork: a handful of
    staircase-style traces running left-to-right, each occasionally
    dropping a small inline component mark. Deterministic (seeded) so
    the pattern is stable frame to frame -- only the cube's rotation
    animates."""
    segments = []
    nodes = []
    u0, u1 = BAND_U_RANGE
    for face_idx, (axis, sign) in enumerate(FACE_SIGNS):
        for band_idx, v_center in enumerate(BAND_V_LEVELS):
            rng = random.Random(face_idx * 1009 + band_idx * 97 + 17)
            level = 0
            u = u0
            pts = [(u, v_center)]
            while u < u1 - 0.02:
                flat_len = rng.uniform(0.16, 0.30)
                u_end = min(u + flat_len, u1)
                cur_v = v_center + level * BAND_STEP_AMP
                pts.append((u_end, cur_v))
                if rng.random() < 0.4:
                    mid_u = (u + u_end) / 2
                    nodes.append((face_point(axis, sign, mid_u, cur_v), rng.randint(0, 1)))
                u = u_end
                if u >= u1 - 0.02:
                    break
                next_level = rng.choice([lv for lv in (-1, 0, 1) if lv != level])
                trans_len = rng.uniform(0.05, 0.09)
                u_trans_end = min(u + trans_len, u1)
                next_v = v_center + next_level * BAND_STEP_AMP
                pts.append((u_trans_end, next_v))
                u = u_trans_end
                level = next_level
            for (ua, va), (ub, vb) in zip(pts, pts[1:]):
                segments.append((face_point(axis, sign, ua, va), face_point(axis, sign, ub, vb)))
    return segments, nodes


FACE_LINES, FACE_NODES = build_face_traces()


class BrailleCanvas:
    """A per-cell dot-occupancy canvas: each terminal cell holds a 2
    (wide) x 4 (tall) grid of sub-cell dots, addressed in dot-space
    coordinates (cell coords * (2,4)). Lines drawn here render as
    Unicode braille glyphs at roughly 8x the spatial detail of a
    plain one-glyph-per-cell buffer."""

    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.dot_w = cols * 2
        self.dot_h = rows * 4
        self.bits = [[0] * cols for _ in range(rows)]

    def set_dot(self, dx, dy):
        dx, dy = int(round(dx)), int(round(dy))
        if 0 <= dx < self.dot_w and 0 <= dy < self.dot_h:
            cell_row, local_row = divmod(dy, 4)
            cell_col, local_col = divmod(dx, 2)
            self.bits[cell_row][cell_col] |= _BRAILLE_BIT[(local_row, local_col)]

    def line_dots(self, x0, y0, x1, y1):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            self.set_dot(x, y)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def thick_line_dots(self, x0, y0, x1, y1, thickness=1):
        """Draw the line plus `thickness` parallel offset copies on
        each side (perpendicular to the stroke), so it reads as a
        solid, bolder line instead of a single dot-wide hairline."""
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            self.set_dot(x0, y0)
            return
        px, py = -dy / length, dx / length
        for off in range(-thickness, thickness + 1):
            ox, oy = px * off, py * off
            self.line_dots(x0 + ox, y0 + oy, x1 + ox, y1 + oy)


def set_cell(chars, attrs, rows, cols, x, y, ch, attr):
    col, row = int(round(x)), int(round(y))
    if 0 <= row < rows and 0 <= col < cols:
        chars[row][col] = ch
        attrs[row][col] = attr


def blit_row(stdscr, row_idx, chars_row, attrs_row, max_col):
    """Draw one row in contiguous same-attribute runs, minimizing the
    number of curses calls per frame."""
    c = 0
    while c < max_col:
        attr = attrs_row[c]
        start = c
        while c < max_col and attrs_row[c] == attr:
            c += 1
        segment = "".join(chars_row[start:c])
        try:
            stdscr.addstr(row_idx, start, segment, attr)
        except curses.error:
            pass


# ------------------------------------------------------- cross-window burst

WINDOW_TAG_PREFIX = "BLACKBOX-"
HYPRCTL_AVAILABLE = shutil.which("hyprctl") is not None


def set_terminal_title(title):
    """Set the owning terminal emulator's window title via the
    standard OSC escape sequence, so Hyprland (or any WM) can identify
    this window by title."""
    try:
        sys.stdout.write(f"\x1b]2;{title}\x07")
        sys.stdout.flush()
    except Exception:
        pass


def hyprctl_clients():
    try:
        result = subprocess.run(
            ["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=0.3
        )
        return json.loads(result.stdout)
    except Exception:
        return []


def find_self_and_others(clients, tag):
    """Split the Hyprland client list into (this window, other Blackbox
    windows) by matching window title."""
    self_client = None
    others = []
    for c in clients:
        title = c.get("title") or ""
        if tag in title:
            if self_client is None:
                self_client = c
            continue
        if title.startswith(WINDOW_TAG_PREFIX):
            others.append(c)
    return self_client, others


def rect_gap(a, b):
    """Shortest edge-to-edge distance (pixels) between two Hyprland
    client rectangles; 0 if they touch or overlap."""
    ax, ay = a["at"]
    aw, ah = a["size"]
    bx, by = b["at"]
    bw, bh = b["size"]
    dx = max(bx - (ax + aw), ax - (bx + bw), 0)
    dy = max(by - (ay + ah), ay - (by + bh), 0)
    return math.hypot(dx, dy)


def apply_burst(chars, attrs, rows, cols, progress, burst_attr):
    """Overlay an expanding, raggedly-edged white flash from the
    screen center, covering everything else once progress reaches 1."""
    if progress <= 0:
        return
    cx, cy = cols / 2.0, rows / 2.0
    max_dist = math.hypot(cols / 2.0, rows) * 1.05
    radius = progress * max_dist
    edge_band = max_dist * 0.06
    for r in range(rows):
        dy = (r - cy) * 2.0
        row_chars, row_attrs = chars[r], attrs[r]
        for c in range(cols):
            dist = math.hypot(c - cx, dy)
            if dist > radius:
                continue
            if dist > radius - edge_band and random.random() < 0.35:
                continue  # ragged leading edge instead of a clean circle
            row_chars[c] = "\u2588"
            row_attrs[c] = burst_attr


def run(stdscr, args):
    curses.curs_set(0)
    curses.use_default_colors()
    stdscr.nodelay(True)
    stdscr.timeout(0)

    burst_attr = curses.A_REVERSE | curses.A_BOLD
    if curses.has_colors():
        try:
            curses.start_color()
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_WHITE)
            burst_attr = curses.color_pair(1) | curses.A_BOLD
        except curses.error:
            pass

    proximity_enabled = args.proximity and HYPRCTL_AVAILABLE
    last_proximity_poll = 0.0
    burst_start = None
    last_burst_end = -1e9
    BURST_EXPAND, BURST_HOLD = 0.5, 0.2
    BURST_TOTAL = BURST_EXPAND + BURST_HOLD

    ax = ay = az = 0.0
    last_status_change = time.time()
    status_idx = 0
    glitch_frames = 0
    frame_interval = 1.0 / max(args.fps, 1)
    orbit_particles = []
    for i in range(args.particles):
        orbit_particles.append({
            "angle": random.uniform(0, math.tau),
            "speed": random.uniform(0.3, 1.1) * (1 if i % 2 == 0 else -1),
            "radius_mult": random.uniform(1.6, 2.6),
            "vtilt": random.uniform(0.3, 1.0),
            "phase": random.uniform(0, math.tau),
        })

    last_frame = time.time()

    while True:
        now = time.time()
        dt = now - last_frame
        if dt < frame_interval:
            time.sleep(max(frame_interval - dt, 0))
            now = time.time()
            dt = now - last_frame
        last_frame = now

        ch = stdscr.getch()
        while ch != -1:
            if ch in (ord("q"), ord("Q"), 27):
                return
            if ch in (ord("b"), ord("B")) and burst_start is None and now - last_burst_end > args.burst_cooldown:
                burst_start = now
                curses.beep()
            ch = stdscr.getch()

        rows, cols = stdscr.getmaxyx()
        if rows < 10 or cols < 30:
            stdscr.erase()
            try:
                stdscr.addstr(0, 0, "terminal too small")
            except curses.error:
                pass
            stdscr.refresh()
            continue

        if proximity_enabled and now - last_proximity_poll >= args.poll_interval:
            last_proximity_poll = now
            clients = hyprctl_clients()
            self_client, others = find_self_and_others(clients, args.window_tag)
            if self_client and others:
                nearest = min(rect_gap(self_client, o) for o in others)
                if (nearest <= args.proximity_threshold and burst_start is None
                        and now - last_burst_end > args.burst_cooldown):
                    burst_start = now
                    curses.beep()

        speed = args.speed
        ax += 0.7 * speed * dt
        ay += 1.1 * speed * dt
        az += 0.31 * speed * dt

        bob = math.sin(now * 0.5) * 0.6

        cx, cy = cols / 2.0, rows / 2.0 + bob
        world_scale = min(rows, cols / 2.0) * 0.38
        cam_dist = 4.5
        aspect_x = 2.05

        def project(px, py, pz):
            rx, ry, rz = rotate(px, py, pz, ax, ay, az)
            persp = cam_dist / (cam_dist + rz)
            sx = cx + rx * persp * world_scale * aspect_x
            sy = cy - ry * persp * world_scale * 0.5 - bob
            return sx, sy, rz

        edge_canvas = BrailleCanvas(rows, cols)
        grid_canvas = BrailleCanvas(rows, cols)

        projected = [project(x, y, z) for (x, y, z) in CUBE_VERTS]
        for a, b in CUBE_EDGES:
            xa, ya, _ = projected[a]
            xb, yb, _ = projected[b]
            edge_canvas.thick_line_dots(xa * 2, ya * 4, xb * 2, yb * 4, thickness=1)

        for (p0, p1) in FACE_LINES:
            xa, ya, _ = project(*p0)
            xb, yb, _ = project(*p1)
            grid_canvas.thick_line_dots(xa * 2, ya * 4, xb * 2, yb * 4, thickness=1)

        chars = [[" "] * cols for _ in range(rows)]
        attrs = [[curses.A_NORMAL] * cols for _ in range(rows)]
        for r in range(rows):
            erow, grow = edge_canvas.bits[r], grid_canvas.bits[r]
            crow, arow = chars[r], attrs[r]
            for c in range(cols):
                eb, gb = erow[c], grow[c]
                if eb or gb:
                    crow[c] = chr(_BRAILLE_BASE + (eb | gb))
                    arow[c] = curses.A_BOLD if gb else curses.A_DIM

        for (nx, ny, nz), parity in FACE_NODES:
            sx, sy, _ = project(nx, ny, nz)
            set_cell(chars, attrs, rows, cols, sx, sy, NODE_GLYPHS[parity], curses.A_NORMAL)

        if args.particles:
            for p in orbit_particles:
                p["angle"] += p["speed"] * dt
                r = world_scale * p["radius_mult"]
                px = cx + math.cos(p["angle"]) * r
                py = cy + math.sin(p["angle"]) * r * p["vtilt"] * 0.5
                depth = math.sin(p["angle"] + p["phase"])
                pch = "\u2022" if depth > 0 else "\u00b7"  # • or ·
                pattr = curses.A_BOLD if depth > 0 else curses.A_DIM
                set_cell(chars, attrs, rows, cols, px, py, pch, pattr)

        if not args.no_glitch:
            if glitch_frames <= 0 and random.random() < 0.006:
                glitch_frames = random.randint(2, 5)
            if glitch_frames > 0:
                glitch_frames -= 1
                box_half = world_scale * 1.3
                for _ in range(int(box_half * 3)):
                    nx = cx + random.uniform(-box_half * aspect_x, box_half * aspect_x)
                    ny = cy + random.uniform(-box_half, box_half)
                    gattr = curses.A_REVERSE if random.random() < 0.3 else curses.A_NORMAL
                    set_cell(chars, attrs, rows, cols, nx, ny, random.choice(GLITCH_GLYPHS), gattr)

        if args.hud and now - last_status_change > 3.5:
            status_idx = (status_idx + 1) % len(STATUS_MESSAGES)
            last_status_change = now

        if burst_start is not None:
            elapsed = now - burst_start
            if elapsed < BURST_EXPAND:
                apply_burst(chars, attrs, rows, cols, elapsed / BURST_EXPAND, burst_attr)
            elif elapsed < BURST_TOTAL:
                apply_burst(chars, attrs, rows, cols, 1.0, burst_attr)
            else:
                burst_start = None
                last_burst_end = now

        stdscr.erase()
        max_col = cols - 1  # leave the bottom-right cell untouched (curses quirk)
        for r in range(rows):
            blit_row(stdscr, r, chars[r], attrs[r], max_col)
        if args.hud and burst_start is None:  # don't clutter the screen with HUD text mid-burst
            cursor_on = int(now * 2) % 2 == 0
            status_line = f"BLACKBOX// {STATUS_MESSAGES[status_idx]}{chr(0x2588) if cursor_on else ' '}"
            coord_line = f"x:{math.sin(ax):+.2f} y:{math.sin(ay):+.2f} z:{math.sin(az):+.2f}"
            try:
                stdscr.addstr(0, 1, status_line[:max_col], curses.A_BOLD)
                stdscr.addstr(0, max(cols - len(coord_line) - 2, len(status_line) + 3),
                              coord_line[:max_col], curses.A_DIM)
            except curses.error:
                pass

        stdscr.noutrefresh()
        curses.doupdate()


def main():
    locale.setlocale(locale.LC_ALL, "")  # needed for curses to emit multi-byte UTF-8 glyphs

    parser = argparse.ArgumentParser(description="A terminal Blackbox screensaver.")
    parser.add_argument("--fps", type=int, default=30, help="frames per second (default: 30)")
    parser.add_argument("--speed", type=float, default=1.0, help="rotation speed multiplier (default: 1.0)")
    parser.add_argument("--particles", type=int, default=10, help="number of orbiting scan particles (default: 10)")
    parser.add_argument("--no-glitch", action="store_true", help="disable occasional static glitch effect")
    parser.add_argument("--hud", action="store_true",
                         help="show the status/coordinate readout text (off by default for a cleaner look)")
    parser.add_argument("--no-proximity", dest="proximity", action="store_false",
                         help="disable cross-window burst detection (Hyprland only)")
    parser.add_argument("--proximity-threshold", type=float, default=80.0,
                         help="pixel gap between windows that triggers a burst (default: 80)")
    parser.add_argument("--poll-interval", type=float, default=0.4,
                         help="seconds between hyprctl window-position checks (default: 0.4)")
    parser.add_argument("--burst-cooldown", type=float, default=6.0,
                         help="minimum seconds between bursts (default: 6.0)")
    parser.set_defaults(proximity=True)
    args = parser.parse_args()

    args.window_tag = f"{WINDOW_TAG_PREFIX}{uuid.uuid4().hex[:6]}"
    set_terminal_title(args.window_tag)

    if args.proximity and not HYPRCTL_AVAILABLE:
        print(
            "note: cross-window burst needs Hyprland's hyprctl, which wasn't found -- "
            "running with the burst effect only reachable via the 'b' key.",
            file=sys.stderr,
        )
        time.sleep(1.5)

    try:
        curses.wrapper(run, args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
