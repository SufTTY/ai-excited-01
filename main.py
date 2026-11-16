#!/usr/bin/env python3
"""
THE PROMINENCE — a colossal solar eruption seen in profile.
Dark space, a blazing solar limb along the bottom, and huge glowing plasma loops
anchored to the limb, with one filament breaking free.
Stdlib-only Python 3, deterministic, 768x768 PNG, runtime < 20 s.
"""

import os
import sys
import math
import random
import zlib
import struct
import time
from array import array
import hashlib

# ===================== CONSTANTS & CONFIG =====================
WIDTH = 768
HEIGHT = 768
SEED = 0xC0FFEEBABE  # fixed seed for determinism
MAKEFUN_OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "out")
OUTPUT_PATH = os.path.join(MAKEFUN_OUTPUT_DIR, "entry.png")

# Limb geometry: circle center far below frame
LIMB_CENTER_Y = HEIGHT * 1.55
LIMB_RADIUS = HEIGHT * 1.18
LIMB_CENTER_X = WIDTH * 0.5

# Anchor points on limb (x as fraction of width)
ANCHOR_A_X = WIDTH * 0.36
ANCHOR_B_X = WIDTH * 0.64

# Loop apex heights (fraction of width above limb)
# Tallest loop is the inner one (index 1) for visual hierarchy
LOOP_APEX_HEIGHTS = [0.52, 0.30, 0.40, 0.46]  # fractions of WIDTH

# Colors (linear HDR, will be tone-mapped)
WHITE_HOT = (1.0, 1.0, 1.0)
ORANGE_GLOW = (255/255, 140/255, 60/255)  # (1.0, 0.55, 0.235)
DEEP_RED = (170/255, 30/255, 25/255)      # (0.667, 0.118, 0.098)
PINK_WHITE = (1.0, 0.85, 0.9)             # coronal rain
LIMB_GLOW = (1.0, 0.5, 0.1)                # limb orange glow
LIMB_CORE = (1.0, 1.0, 0.95)               # limb white-hot core
SPIKULE_COLOR = (1.0, 0.6, 0.15)           # spicules

# ===================== UTILITY FUNCTIONS =====================

def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v

def lerp(a, b, t):
    return a + (b - a) * t

def smoothstep(edge0, edge1, x):
    t = clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)

def gaussian(d, sigma):
    return math.exp(-0.5 * (d / sigma) ** 2)

def gaussian2d(dx, dy, sigma):
    return math.exp(-0.5 * (dx*dx + dy*dy) / (sigma*sigma))

def hdr_to_srgb(hdr):
    """Reinhard tone mapping + gamma 2.2, returns 0-255 int."""
    mapped = hdr / (1.0 + hdr)
    linear = mapped ** (1.0 / 2.2)
    return int(clamp(linear, 0.0, 1.0) * 255.0 + 0.5)

def add_color(buf, x, y, color, weight=1.0):
    """Additive blending into HDR buffer."""
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        buf[y][x][0] += color[0] * weight
        buf[y][x][1] += color[1] * weight
        buf[y][x][2] += color[2] * weight

# ===================== NOISE: VALUE NOISE (BILINEAR) =====================

class ValueNoise2D:
    """2D value noise with bilinear interpolation, deterministic."""
    def __init__(self, seed, grid_size=256):
        self.grid_size = grid_size
        self.mask = grid_size - 1
        rng = random.Random(seed)
        self.perm = list(range(grid_size))
        rng.shuffle(self.perm)
        self.perm += self.perm
        self.values = [rng.random() for _ in range(grid_size * grid_size)]

    def _index(self, x, y):
        return (y & self.mask) * self.grid_size + (x & self.mask)

    def sample(self, x, y):
        x0 = int(math.floor(x)) & self.mask
        y0 = int(math.floor(y)) & self.mask
        x1 = (x0 + 1) & self.mask
        y1 = (y0 + 1) & self.mask

        tx = x - math.floor(x)
        ty = y - math.floor(y)

        sx = tx * tx * (3.0 - 2.0 * tx)
        sy = ty * ty * (3.0 - 2.0 * ty)

        v00 = self.values[self._index(x0, y0)]
        v10 = self.values[self._index(x1, y0)]
        v01 = self.values[self._index(x0, y1)]
        v11 = self.values[self._index(x1, y1)]

        v0 = lerp(v00, v10, sx)
        v1 = lerp(v01, v11, sx)
        return lerp(v0, v1, sy)

    def fractal(self, x, y, octaves=3, persistence=0.5, lacunarity=2.0):
        value = 0.0
        amplitude = 1.0
        frequency = 1.0
        max_value = 0.0
        for _ in range(octaves):
            value += amplitude * self.sample(x * frequency, y * frequency)
            max_value += amplitude
            amplitude *= persistence
            frequency *= lacunarity
        return value / max_value if max_value > 0 else 0.0


class ValueNoise1D:
    """1D value noise for parametric turbulence."""
    def __init__(self, seed, size=1024):
        self.size = size
        self.mask = size - 1
        rng = random.Random(seed)
        self.values = [rng.random() for _ in range(size)]

    def sample(self, x):
        x0 = int(math.floor(x)) & self.mask
        x1 = (x0 + 1) & self.mask
        tx = x - math.floor(x)
        sx = tx * tx * (3.0 - 2.0 * tx)
        return lerp(self.values[x0], self.values[x1], sx)

    def fractal(self, x, octaves=4, persistence=0.5, lacunarity=2.0):
        value = 0.0
        amplitude = 1.0
        frequency = 1.0
        max_value = 0.0
        for _ in range(octaves):
            value += amplitude * self.sample(x * frequency)
            max_value += amplitude
            amplitude *= persistence
            frequency *= lacunarity
        return value / max_value if max_value > 0 else 0.0

# ===================== GEOMETRY HELPERS =====================

def point_on_limb(x):
    """Get y coordinate of limb circle at given x."""
    dx = x - LIMB_CENTER_X
    if dx*dx > LIMB_RADIUS*LIMB_RADIUS:
        return None
    dy = math.sqrt(LIMB_RADIUS*LIMB_RADIUS - dx*dx)
    return LIMB_CENTER_Y - dy  # upper half of circle

def limb_normal(x, y):
    """Unit normal pointing outward from limb circle at (x,y)."""
    dx = x - LIMB_CENTER_X
    dy = y - LIMB_CENTER_Y
    length = math.hypot(dx, dy)
    if length == 0:
        return (0.0, -1.0)
    return (dx / length, dy / length)

def loop_arc(anchor_a, anchor_b, apex_height, t, noise1d, noise_scale=0.08):
    """
    Parametric point on a smooth arch from anchor_a to anchor_b.
    t in [0, 1]. Uses sine arch with superellipse shaping + noise wobble.
    """
    ax, ay = anchor_a
    bx, by = anchor_b

    # Base sine arch
    base_x = lerp(ax, bx, t)
    base_y = lerp(ay, by, t) - apex_height * math.sin(math.pi * t)

    # Noise wobble in position (perpendicular to arch)
    # Use parametric angle for coherent noise along loop
    noise_param = t * 20.0
    n1 = noise1d.fractal(noise_param, octaves=3) * noise_scale
    n2 = noise1d.fractal(noise_param + 100.0, octaves=3) * noise_scale

    # Tangent computed analytically (derivative of base arch)
    # base_x = ax + (bx-ax)*t, derivative = bx-ax
    # base_y = ay + (by-ay)*t - apex_height*sin(pi*t), derivative = (by-ay) - apex_height*pi*cos(pi*t)
    tx = bx - ax
    ty = (by - ay) - apex_height * math.pi * math.cos(math.pi * t)
    tlen = math.hypot(tx, ty)
    if tlen > 0:
        nx, ny = -ty / tlen, tx / tlen  # normal (perpendicular)
        return (base_x + nx * n1 * WIDTH, base_y + ny * n1 * WIDTH)
    return (base_x, base_y)

def loop_width(t, base_width, noise1d):
    """Loop width with smooth noise variation."""
    # Base: wider at center, narrow at anchors
    base = base_width * (1.0 + 0.5 * math.sin(math.pi * t))
    n = noise1d.fractal(t * 15.0 + 50.0, octaves=2) * 0.3
    return base * (1.0 + n)

# ===================== MAIN RENDERER =====================

def render():
    """Render the full scene into an HDR float buffer [H][W][3]."""
    start_time = time.time()

    # Initialize HDR buffer
    hdr = [[[0.0, 0.0, 0.0] for _ in range(WIDTH)] for _ in range(HEIGHT)]

    # Noise instances
    sky_noise = ValueNoise2D(SEED ^ 0x111111, 256)
    star_noise = ValueNoise2D(SEED ^ 0x222222, 256)
    limb_granulation = ValueNoise2D(SEED ^ 0x333333, 512)
    limb_spicule = ValueNoise1D(SEED ^ 0x444444, 1024)
    loop_noise = [ValueNoise1D(SEED ^ (0x555555 + i), 1024) for i in range(4)]
    rain_noise = ValueNoise1D(SEED ^ 0x666666, 1024)
    filament_noise = ValueNoise1D(SEED ^ 0x777777, 1024)

    # Precompute anchor y positions on limb
    anchor_a_y = point_on_limb(ANCHOR_A_X)
    anchor_b_y = point_on_limb(ANCHOR_B_X)
    anchor_a = (ANCHOR_A_X, anchor_a_y)
    anchor_b = (ANCHOR_B_X, anchor_b_y)

    # ==================== 1. SKY GRADIENT + STARS ====================
    # Upper ~70%: near-black with very slight deep-red vertical gradient
    # getting brighter toward the limb

    for y in range(HEIGHT):
        # Vertical gradient: darker at top, slightly brighter near limb
        # Sky occupies y < limb_y - 50 roughly
        t = y / (HEIGHT - 1)
        # Deep red gradient: nearly black at top (0.001), very dark red near limb (0.015)
        r_bg = lerp(0.0005, 0.012, t)
        g_bg = lerp(0.0001, 0.002, t)
        b_bg = lerp(0.0001, 0.0005, t)

        # Very subtle noise texture in sky
        ny = y * 0.0015
        for x in range(WIDTH):
            nx = x * 0.0015
            n = sky_noise.fractal(nx, ny, octaves=2, persistence=0.5) * 0.0015
            hdr[y][x][0] = r_bg + n * 0.5
            hdr[y][x][1] = g_bg + n * 0.2
            hdr[y][x][2] = b_bg + n * 0.1

    # Stars: ~250 tiny faint stars, no cross sparkles > 3px
    star_rng = random.Random(SEED ^ 0x888888)
    num_stars = 250

    for _ in range(num_stars):
        # Uniform distribution but favor upper portion
        sx = star_rng.random() * WIDTH
        sy = star_rng.random() * HEIGHT * 0.75  # upper 75%

        # Skip if too close to limb
        limb_y = point_on_limb(sx)
        if limb_y is not None and sy > limb_y - 30:
            continue

        size = 0.3 + star_rng.random() * 0.6  # 0.3-0.9 px
        brightness = 0.001 + star_rng.random() * 0.004  # very faint
        temp = star_rng.random()
        if temp < 0.15:
            color = (brightness * 1.1, brightness * 0.9, brightness * 0.8)
        elif temp < 0.3:
            color = (brightness * 0.9, brightness * 0.95, brightness * 1.1)
        else:
            color = (brightness, brightness, brightness)

        ix = int(sx)
        iy = int(sy)
        radius_px = int(size * 3) + 1
        for yy in range(max(0, iy - radius_px), min(HEIGHT, iy + radius_px + 1)):
            dy = yy - sy
            dy2 = dy * dy
            for xx in range(max(0, ix - radius_px), min(WIDTH, ix + radius_px + 1)):
                dx = xx - sx
                d2 = dx*dx + dy2
                g = math.exp(-0.5 * d2 / (size*size))
                if g > 0.001:
                    hdr[yy][xx][0] += color[0] * g
                    hdr[yy][xx][1] += color[1] * g
                    hdr[yy][xx][2] += color[2] * g

    # ==================== 2. SOLAR LIMB ====================
    # Disk interior: deep red-orange with granulation, brightening toward limb
    # Limb itself: 2-3 px white-hot line, ~8 px orange glow above, spicules

    limb_y_at_center = point_on_limb(LIMB_CENTER_X)
    print(f"Limb at center y={limb_y_at_center:.1f}", file=sys.stderr)

    # Process each pixel for limb and disk
    for y in range(HEIGHT):
        for x in range(WIDTH):
            dx = x - LIMB_CENTER_X
            dy = y - LIMB_CENTER_Y
            dist_to_center = math.hypot(dx, dy)

            if dist_to_center < LIMB_RADIUS:
                # Inside solar disk
                # Distance from limb (negative inside)
                d_from_limb = dist_to_center - LIMB_RADIUS  # negative inside

                # Normalized depth into disk (0 at limb, 1 at center)
                depth = clamp(-d_from_limb / (LIMB_RADIUS * 0.15), 0.0, 1.0)

                # Granulation noise
                nx = x * 0.008
                ny = y * 0.008
                gran = limb_granulation.fractal(nx, ny, octaves=3) * 0.15

                # Base color: deep red-orange, brightening toward limb
                # At limb: (0.9, 0.3, 0.05), at depth 0.15: (0.5, 0.1, 0.02)
                r_base = lerp(0.9, 0.5, depth) + gran
                g_base = lerp(0.3, 0.1, depth) + gran * 0.5
                b_base = lerp(0.05, 0.02, depth) + gran * 0.2

                hdr[y][x][0] = r_base
                hdr[y][x][1] = g_base
                hdr[y][x][2] = b_base

            elif dist_to_center < LIMB_RADIUS + 24:
                # Limb region: glow, core line, spicules
                d_from_limb = dist_to_center - LIMB_RADIUS  # positive outside

                # Normal at this point
                nx, ny = dx / dist_to_center, dy / dist_to_center

                # 1. White-hot limb core line: 2-3 px thick at d≈0
                core_falloff = gaussian(d_from_limb, 1.2)  # ~2.4 px FWHM
                if core_falloff > 0.01:
                    hdr[y][x][0] += LIMB_CORE[0] * core_falloff * 2.5
                    hdr[y][x][1] += LIMB_CORE[1] * core_falloff * 2.5
                    hdr[y][x][2] += LIMB_CORE[2] * core_falloff * 2.5

                # 2. Orange glow above limb: ~8 px, only on sunward side (ny < 0)
                if ny < 0 and d_from_limb < 12:
                    glow_falloff = gaussian(d_from_limb, 3.5) * (-ny)  # stronger toward sun
                    if glow_falloff > 0.01:
                        hdr[y][x][0] += LIMB_GLOW[0] * glow_falloff * 1.2
                        hdr[y][x][1] += LIMB_GLOW[1] * glow_falloff * 1.2
                        hdr[y][x][2] += LIMB_GLOW[2] * glow_falloff * 1.2

                # 3. Spicules: fine vertical combs along limb
                # Only very close to limb (d < 8), modulated by 1D noise along limb angle
                if d_from_limb < 8:
                    angle = math.atan2(dy, dx)
                    # Map angle to noise coordinate
                    noise_coord = angle * 120.0
                    spic = limb_spicule.fractal(noise_coord, octaves=4) * 0.8 + 0.2
                    # Spicules point radially outward
                    spic_falloff = gaussian(d_from_limb, 1.5) * spic
                    if spic_falloff > 0.02:
                        hdr[y][x][0] += SPIKULE_COLOR[0] * spic_falloff * 0.6
                        hdr[y][x][1] += SPIKULE_COLOR[1] * spic_falloff * 0.6
                        hdr[y][x][2] += SPIKULE_COLOR[2] * spic_falloff * 0.6

    # ==================== 3. PROMINENCE LOOPS ====================
    # Four nested loops from anchor A to anchor B
    # Each rendered in three additive passes: core, inner glow, outer falloff

    # Determine loop order: innermost (index 0) is tallest for visual hierarchy
    loop_order = [1, 0, 3, 2]  # heights: 0.30, 0.52, 0.46, 0.40

    for loop_idx in loop_order:
        apex_h = LOOP_APEX_HEIGHTS[loop_idx] * WIDTH
        noise1d = loop_noise[loop_idx]

        # Base widths for three passes (in pixels)
        core_width = 2.5
        inner_glow_width = 7.0
        outer_glow_width = 18.0

        # Sample loop at many points and draw gaussian tubes
        num_segments = 300
        for seg in range(num_segments):
            t = seg / (num_segments - 1)
            px, py = loop_arc(anchor_a, anchor_b, apex_h, t, noise1d)
            w = loop_width(t, 1.0, noise1d)

            # Draw gaussian cross-section at this point
            ix = int(px)
            iy = int(py)

            # Core pass: 2-3 px white-hot
            sigma_core = core_width * w * 0.6
            radius_core = int(sigma_core * 3) + 1
            for yy in range(max(0, iy - radius_core), min(HEIGHT, iy + radius_core + 1)):
                dy = yy - py
                for xx in range(max(0, ix - radius_core), min(WIDTH, ix + radius_core + 1)):
                    dx = xx - px
                    d2 = dx*dx + dy*dy
                    g = math.exp(-0.5 * d2 / (sigma_core*sigma_core))
                    if g > 0.001:
                        hdr[yy][xx][0] += WHITE_HOT[0] * g * 3.0
                        hdr[yy][xx][1] += WHITE_HOT[1] * g * 3.0
                        hdr[yy][xx][2] += WHITE_HOT[2] * g * 3.0

            # Inner glow pass: ~6-8 px orange
            sigma_inner = inner_glow_width * w * 0.5
            radius_inner = int(sigma_inner * 3) + 1
            for yy in range(max(0, iy - radius_inner), min(HEIGHT, iy + radius_inner + 1)):
                dy = yy - py
                for xx in range(max(0, ix - radius_inner), min(WIDTH, ix + radius_inner + 1)):
                    dx = xx - px
                    d2 = dx*dx + dy*dy
                    g = math.exp(-0.5 * d2 / (sigma_inner*sigma_inner))
                    if g > 0.001:
                        hdr[yy][xx][0] += ORANGE_GLOW[0] * g * 1.5
                        hdr[yy][xx][1] += ORANGE_GLOW[1] * g * 1.5
                        hdr[yy][xx][2] += ORANGE_GLOW[2] * g * 1.5

            # Outer falloff pass: ~16-20 px deep red
            sigma_outer = outer_glow_width * w * 0.5
            radius_outer = int(sigma_outer * 3) + 1
            for yy in range(max(0, iy - radius_outer), min(HEIGHT, iy + radius_outer + 1)):
                dy = yy - py
                for xx in range(max(0, ix - radius_outer), min(WIDTH, ix + radius_outer + 1)):
                    dx = xx - px
                    d2 = dx*dx + dy*dy
                    g = math.exp(-0.5 * d2 / (sigma_outer*sigma_outer))
                    if g > 0.001:
                        hdr[yy][xx][0] += DEEP_RED[0] * g * 0.8
                        hdr[yy][xx][1] += DEEP_RED[1] * g * 0.8
                        hdr[yy][xx][2] += DEEP_RED[2] * g * 0.8

    # ==================== 4. CORONAL RAIN ====================
    # ~150 small bright pink-white gaussian blobs along lower half of loops
    # Focus on the tallest loop (index 1, apex_h = 0.30 W)

    rain_loop_idx = 1  # the tallest loop
    rain_apex_h = LOOP_APEX_HEIGHTS[rain_loop_idx] * WIDTH
    rain_noise1d = loop_noise[rain_loop_idx]

    num_rain = 150
    for i in range(num_rain):
        # Distribute along lower half (t from 0.1 to 0.6, avoiding anchors)
        t = 0.1 + (i / num_rain) * 0.5
        # Add some noise to position
        t += (rain_noise.fractal(i * 0.5, octaves=2) - 0.5) * 0.05
        t = clamp(t, 0.1, 0.6)

        px, py = loop_arc(anchor_a, anchor_b, rain_apex_h, t, rain_noise1d)

        # Small gaussian blob
        size = 1.0 + rain_noise.fractal(i * 1.3, octaves=1) * 0.8
        brightness = 0.5 + rain_noise.fractal(i * 2.1, octaves=1) * 0.5

        ix = int(px)
        iy = int(py)
        radius_px = int(size * 3) + 1
        for yy in range(max(0, iy - radius_px), min(HEIGHT, iy + radius_px + 1)):
            dy = yy - py
            for xx in range(max(0, ix - radius_px), min(WIDTH, ix + radius_px + 1)):
                dx = xx - px
                d2 = dx*dx + dy*dy
                g = math.exp(-0.5 * d2 / (size*size))
                if g > 0.01:
                    hdr[yy][xx][0] += PINK_WHITE[0] * g * brightness * 1.5
                    hdr[yy][xx][1] += PINK_WHITE[1] * g * brightness * 1.5
                    hdr[yy][xx][2] += PINK_WHITE[2] * g * brightness * 1.5

    # ==================== 5. ERUPTING FILAMENT ====================
    # From tallest loop's apex, a filament escapes upward-left
    # Spirals/unwinds upward with shrinking radius
    # Same three-pass glow + stretched wisps along tangent

    filament_apex_h = LOOP_APEX_HEIGHTS[1] * WIDTH  # tallest loop
    # Apex is at t=0.5
    apex_x, apex_y = loop_arc(anchor_a, anchor_b, filament_apex_h, 0.5, loop_noise[1])

    # Filament path: spiral unwinding upward-left
    # Parametric: starts at apex, curves up-left with decreasing radius
    num_filament_points = 200
    for i in range(num_filament_points):
        t = i / (num_filament_points - 1)

        # Spiral parameters
        # Start at apex, spiral up-left
        angle = math.pi * 0.5 + t * 4.0 * math.pi  # 2 full turns
        radius = filament_apex_h * 0.35 * (1.0 - t * 0.85)  # shrinking

        # Center of spiral drifts up-left from apex
        cx = apex_x - t * WIDTH * 0.25
        cy = apex_y - t * WIDTH * 0.45

        fx = cx + radius * math.cos(angle)
        fy = cy + radius * math.sin(angle)

        # Stop if off canvas
        if fx < -20 or fx > WIDTH + 20 or fy < -20 or fy > HEIGHT + 20:
            break

        # Width shrinks along filament
        base_w = 1.0 - t * 0.7
        w = base_w * (0.7 + filament_noise.fractal(t * 10.0, octaves=2) * 0.3)

        # Three passes
        # Core
        sigma_c = 2.0 * w
        rc = int(sigma_c * 3) + 1
        for yy in range(max(0, int(fy) - rc), min(HEIGHT, int(fy) + rc + 1)):
            dy = yy - fy
            for xx in range(max(0, int(fx) - rc), min(WIDTH, int(fx) + rc + 1)):
                dx = xx - fx
                d2 = dx*dx + dy*dy
                g = math.exp(-0.5 * d2 / (sigma_c*sigma_c))
                if g > 0.001:
                    hdr[yy][xx][0] += WHITE_HOT[0] * g * 2.5
                    hdr[yy][xx][1] += WHITE_HOT[1] * g * 2.5
                    hdr[yy][xx][2] += WHITE_HOT[2] * g * 2.5

        # Inner glow
        sigma_i = 6.0 * w
        ri = int(sigma_i * 3) + 1
        for yy in range(max(0, int(fy) - ri), min(HEIGHT, int(fy) + ri + 1)):
            dy = yy - fy
            for xx in range(max(0, int(fx) - ri), min(WIDTH, int(fx) + ri + 1)):
                dx = xx - fx
                d2 = dx*dx + dy*dy
                g = math.exp(-0.5 * d2 / (sigma_i*sigma_i))
                if g > 0.001:
                    hdr[yy][xx][0] += ORANGE_GLOW[0] * g * 1.2
                    hdr[yy][xx][1] += ORANGE_GLOW[1] * g * 1.2
                    hdr[yy][xx][2] += ORANGE_GLOW[2] * g * 1.2

        # Outer falloff
        sigma_o = 16.0 * w
        ro = int(sigma_o * 3) + 1
        for yy in range(max(0, int(fy) - ro), min(HEIGHT, int(fy) + ro + 1)):
            dy = yy - fy
            for xx in range(max(0, int(fx) - ro), min(WIDTH, int(fx) + ro + 1)):
                dx = xx - fx
                d2 = dx*dx + dy*dy
                g = math.exp(-0.5 * d2 / (sigma_o*sigma_o))
                if g > 0.001:
                    hdr[yy][xx][0] += DEEP_RED[0] * g * 0.6
                    hdr[yy][xx][1] += DEEP_RED[1] * g * 0.6
                    hdr[yy][xx][2] += DEEP_RED[2] * g * 0.6

        # Stretched wisps: short bright streaks along tangent
        if i > 5 and i < num_filament_points - 5:
            # Tangent direction
            if i > 0:
                px, py = loop_arc(anchor_a, anchor_b, filament_apex_h, 0.5, loop_noise[1])
                # Actually use previous point for tangent
                pt = (i - 1) / (num_filament_points - 1)
                p_angle = math.pi * 0.5 + pt * 4.0 * math.pi
                p_radius = filament_apex_h * 0.35 * (1.0 - pt * 0.85)
                p_cx = apex_x - pt * WIDTH * 0.25
                p_cy = apex_y - pt * WIDTH * 0.45
                pfx = p_cx + p_radius * math.cos(p_angle)
                pfy = p_cy + p_radius * math.sin(p_angle)

                tx = fx - pfx
                ty = fy - pfy
                tlen = math.hypot(tx, ty)
                if tlen > 0:
                    tx, ty = tx / tlen, ty / tlen
                    # Draw a few streaks perpendicular to tangent (wispy)
                    for wisp in range(2):
                        wx = fx + filament_noise.fractal(i * 3.0 + wisp * 100.0, octaves=1) * 8.0 * tx
                        wy = fy + filament_noise.fractal(i * 3.0 + wisp * 100.0 + 50.0, octaves=1) * 8.0 * ty
                        # Perpendicular offset
                        px_off = -ty * filament_noise.fractal(i * 3.0 + wisp * 100.0 + 200.0, octaves=1) * 4.0
                        py_off = tx * filament_noise.fractal(i * 3.0 + wisp * 100.0 + 300.0, octaves=1) * 4.0

                        wispx = wx + px_off
                        wispy = wy + py_off

                        if 0 <= wispx < WIDTH and 0 <= wispy < HEIGHT:
                            sigma_w = 1.5 * w
                            rw = int(sigma_w * 3) + 1
                            for yy in range(max(0, int(wispy) - rw), min(HEIGHT, int(wispy) + rw + 1)):
                                dy = yy - wispy
                                for xx in range(max(0, int(wispx) - rw), min(WIDTH, int(wispx) + rw + 1)):
                                    dx = xx - wispx
                                    d2 = dx*dx + dy*dy
                                    g = math.exp(-0.5 * d2 / (sigma_w*sigma_w))
                                    if g > 0.01:
                                        hdr[yy][xx][0] += WHITE_HOT[0] * g * 1.5
                                        hdr[yy][xx][1] += WHITE_HOT[1] * g * 1.5
                                        hdr[yy][xx][2] += WHITE_HOT[2] * g * 1.5

    elapsed = time.time() - start_time
    print(f"Render time: {elapsed:.2f}s", file=sys.stderr)
    return hdr


# ===================== PNG WRITER =====================

def write_png(hdr, path):
    """Write HDR buffer as sRGB PNG with tone mapping."""
    # Convert to sRGB bytes
    rows = []
    for y in range(HEIGHT):
        row = bytearray([0])  # filter type 0
        for x in range(WIDTH):
            r = hdr_to_srgb(hdr[y][x][0])
            g = hdr_to_srgb(hdr[y][x][1])
            b = hdr_to_srgb(hdr[y][x][2])
            row.extend([r, g, b])
        rows.append(row)

    # Compress
    raw = b''.join(rows)
    compressed = zlib.compress(raw, level=9)

    # PNG chunks
    def chunk(chunk_type, data):
        crc = zlib.crc32(chunk_type + data) & 0xffffffff
        return struct.pack('>I', len(data)) + chunk_type + data + struct.pack('>I', crc)

    png = bytearray()
    png += b'\x89PNG\r\n\x1a\n'  # signature
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', WIDTH, HEIGHT, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', compressed)
    png += chunk(b'IEND', b'')

    with open(path, 'wb') as f:
        f.write(png)

    return len(png)


# ===================== ANALYSIS =====================

def analyze(hdr):
    """Print acceptance check metrics."""
    # Collect luminances
    sky_lums = []
    all_lums = []
    max_lum = 0.0
    max_pos = (0, 0)

    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = hdr[y][x]
            # Linear luminance (Rec. 709)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            all_lums.append(lum)
            if lum > max_lum:
                max_lum = lum
                max_pos = (x, y)
            # Sky region: upper 70%
            if y < HEIGHT * 0.7:
                sky_lums.append(lum)

    all_lums.sort()
    sky_lums.sort()

    global_median = all_lums[len(all_lums) // 2]
    global_mean = sum(all_lums) / len(all_lums)
    sky_mean = sum(sky_lums) / len(sky_lums) if sky_lums else 0

    print(f"\n=== ACCEPTANCE CHECKS ===")
    print(f"1. Sky mean luminance < 0.06: {'PASS' if sky_mean < 0.06 else 'FAIL'} ({sky_mean:.4f})")
    print(f"   Global median luminance < 0.10: {'PASS' if global_median < 0.10 else 'FAIL'} ({global_median:.4f})")
    print(f"2. Brightest pixel at ({max_pos[0]}, {max_pos[1]}): {max_lum:.4f}")
    print(f"   Limb/loop cores should be brightest")
    print(f"3. Sky flatness: sky mean = {sky_mean:.4f} (should be flat, no radial streaks)")
    print(f"   Global mean = {global_mean:.4f}")

    return sky_mean, global_median, max_lum


# ===================== MAIN =====================

def main():
    os.makedirs(MAKEFUN_OUTPUT_DIR, exist_ok=True)

    print("Rendering THE PROMINENCE...", file=sys.stderr)
    hdr = render()

    print("Analyzing...", file=sys.stderr)
    sky_mean, global_median, max_lum = analyze(hdr)

    print("Writing PNG...", file=sys.stderr)
    png_size = write_png(hdr, OUTPUT_PATH)

    # Verify byte-identical on second run
    print("Verifying determinism...", file=sys.stderr)
    hdr2 = render()
    png_size2 = write_png(hdr2, OUTPUT_PATH + ".verify")

    with open(OUTPUT_PATH, 'rb') as f1, open(OUTPUT_PATH + ".verify", 'rb') as f2:
        hash1 = hashlib.sha256(f1.read()).hexdigest()
        hash2 = hashlib.sha256(f2.read()).hexdigest()

    os.remove(OUTPUT_PATH + ".verify")

    print(f"\n=== FINAL RESULTS ===")
    print(f"PNG size: {png_size} bytes ({'<= 1048576' if png_size <= 1048576 else 'EXCEEDS LIMIT'})")
    print(f"SHA256: {hash1}")
    print(f"Deterministic: {'PASS' if hash1 == hash2 else 'FAIL'}")
    print(f"Runtime: check render time above (< 20s required)")

    # Final acceptance summary
    checks = [
        ("Sky mean < 0.06", sky_mean < 0.06),
        ("Global median < 0.10", global_median < 0.10),
        ("PNG size <= 1MB", png_size <= 1048576),
        ("Deterministic (SHA256 match)", hash1 == hash2),
    ]
    all_pass = all(c[1] for c in checks)
    print(f"\nAll checks: {'PASS' if all_pass else 'FAIL'}")
    for name, result in checks:
        print(f"  {name}: {'PASS' if result else 'FAIL'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())