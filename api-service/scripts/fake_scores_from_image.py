# ⚠️ FAKE data script: uses one satellite image to "fake" the scoring model's output, for the M2 demo. Delete the whole thing once the real model is connected.
# How: crop the satellite image to a square → cut it into 100×100 tiles → combine two layers of info into 20-dimension scores:
#   1) Pixel color analysis (automatic): how much of each tile is water, trees, grass, sand/dirt, running track, gray (roofs/pavement)
#   2) Manual labels (rough areas drawn by hand): things color can't show, like historic buildings, cultural streets, landmarks, main roads
# Output: data/fake/charles_river_scores.npz (read by fake_model.py, score range 0-1) + one 20-dimension preview image (for people to check)
# Dimension names are read from api/dimensions.json; each dimension's formula below is written by name, so after adding/removing dimensions, add or delete the matching formula in build_scores()
# Run: docker compose exec api-service /.venv/bin/python scripts/fake_scores_from_image.py
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Paths and grid size
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "fake"
IMAGE_PATH = DATA_DIR / "charles_river_satellite.webp"
OUT_SCORES = DATA_DIR / "charles_river_scores.npz"
OUT_PREVIEW = DATA_DIR / "charles_river_preview.png"
GRID = 100  # 100×100 tiles
PX = 10  # each tile takes 10×10 pixels on the resized image
SEED = 215  # random seed for the noise, so every run gives the same result

# The dimension table shared by the whole team (the same file api-service reads)
DIMS_PATH = Path(__file__).resolve().parent.parent / "api" / "dimensions.json"
DIMENSION_NAMES = list(json.loads(DIMS_PATH.read_text(encoding="utf-8"))["dimensions"])

# Grid coordinates: rr is the row number, cc is the column number (0-99); all labels use this coordinate system
rr, cc = np.mgrid[0:GRID, 0:GRID].astype(float)


# ---------------- 1) Read the image, cut into tiles ----------------
# Read the image → crop the middle square → resize to 1000×1000 (10×10 pixels per tile)
def load_square_image() -> np.ndarray:
    im = Image.open(IMAGE_PATH).convert("RGB")
    w, h = im.size
    s = min(w, h)
    x0, y0 = (w - s) // 2, (h - s) // 2
    im = im.crop((x0, y0, x0 + s, y0 + s)).resize((GRID * PX, GRID * PX), Image.LANCZOS)
    return np.asarray(im).astype(float)


# Pixel-level mask (0/1) → share per tile (0-1): average the 10×10 pixels
def per_tile(mask: np.ndarray) -> np.ndarray:
    return mask.reshape(GRID, PX, GRID, PX).mean(axis=(1, 3))


# ---------------- 2) Pixel color classes ----------------
# Sort each pixel by color into: water / trees / grass / sand-dirt / running track / gray built-up area / big white roofs
def classify_pixels(img: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    bright = img.max(axis=-1)
    sat = bright - img.min(axis=-1)  # rough saturation: max channel - min channel

    water = (bright < 75) & (b > r + 12) & (b >= g)  # very dark, bluish: river water
    veg = (g > r * 1.06) & (g > b * 1.04) & ~water  # green wins: vegetation
    tree = veg & (bright < 115)  # dark green: tree canopy
    grass = veg & (bright >= 115)  # light green/teal: lawns, sports fields
    bare = (r > g) & (g > b) & (r - b > 28) & (bright > 135) & ~veg  # beige/khaki: sand, construction sites, infields
    track = (r > g + 45) & (r > b + 45)  # brick red: running tracks, red roofs
    built = ~(water | veg | bare | track)  # the gray tones left over: roofs, pavement, parking lots
    white = built & (bright > 200) & (sat < 30)  # very bright white: big flat roofs
    return {k: per_tile(v.astype(float)) for k, v in {
        "water": water, "tree": tree, "grass": grass, "bare": bare,
        "track": track, "built": built, "white": white,
    }.items()}


# The "texture" of each tile: std of brightness (small houses + trees in neighborhoods = busy texture; sports fields, water = flat texture)
def texture(img: np.ndarray) -> np.ndarray:
    lum = img.mean(axis=-1).reshape(GRID, PX, GRID, PX)
    t = lum.std(axis=(1, 3))
    return np.clip(t / np.percentile(t, 95), 0, 1)


# ---------------- 3) Small tools on the grid ----------------
# Box blur: replace each tile with the average of the (2r+1)×(2r+1) tiles around it ("what you can see nearby when riding here")
def blur(grid: np.ndarray, r: int) -> np.ndarray:
    p = np.pad(grid, r, mode="edge")
    out = np.zeros_like(grid)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out += p[r + dy:r + dy + GRID, r + dx:r + dx + GRID]
    return out / (2 * r + 1) ** 2


# The river is cut into several pieces by bridges: keep every connected water area that's big enough (≥ MIN_RIVER_TILES tiles),
# and remove scattered small bits (dark roofs and shadows mistaken for water)
MIN_RIVER_TILES = 25


def keep_river(water: np.ndarray) -> np.ndarray:
    seen = np.zeros((GRID, GRID), bool)
    river = np.zeros((GRID, GRID))
    for sr, sc in zip(*np.nonzero(water > 0.4)):
        if seen[sr, sc]:
            continue
        # Start from this tile and find all water tiles connected up/down/left/right (flood fill)
        stack, comp = [(sr, sc)], []
        seen[sr, sc] = True
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < GRID and 0 <= nx < GRID and not seen[ny, nx] and water[ny, nx] > 0.4:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        # Only big enough counts as river
        if len(comp) >= MIN_RIVER_TILES:
            for y, x in comp:
                river[y, x] = 1
    # The ring of tiles along the river also keeps its own water share (half-water tiles on the bank)
    near = blur(river, 1) > 0
    return np.where(near, np.maximum(water, river), 0)


# Soft ellipse: center = 1, smoothly drops to 0 going out (used to draw the hand-labeled areas)
def ellipse(row, col, r_row, r_col, strength=1.0):
    d2 = ((rr - row) / r_row) ** 2 + ((cc - col) / r_col) ** 2
    return strength * np.exp(-d2 * 1.5)


# Soft polyline: the closer to the line, the closer to 1 (used to draw roads); width is the half-width (in tiles)
def polyline(points, width=1.2, strength=1.0):
    best = np.full((GRID, GRID), np.inf)
    for (r0, c0), (r1, c1) in zip(points[:-1], points[1:]):
        vr, vc = r1 - r0, c1 - c0
        t = np.clip(((rr - r0) * vr + (cc - c0) * vc) / (vr * vr + vc * vc), 0, 1)
        d = np.hypot(rr - (r0 + t * vr), cc - (c0 + t * vc))
        best = np.minimum(best, d)
    return strength * np.exp(-((best / width) ** 2))


# Several labels stacked together: take the max (a spot labeled twice still won't go over 1)
def union(*layers):
    return np.clip(np.max(np.stack(layers), axis=0), 0, 1)


# ---------------- 4) Manual labels (rough areas drawn by hand from the image, coordinates = (row, col)) ----------------
def annotations() -> dict[str, np.ndarray]:
    # Main roads: Mass Pike, interchange ramps, riverside roads on both banks, Western Ave
    mass_pike = polyline([(87, 0), (88, 20), (86, 45), (82, 60), (76, 68)], width=1.8)
    ramps = union(
        polyline([(82, 55), (72, 58), (66, 62), (62, 67)], width=1.4, strength=0.9),
        polyline([(86, 45), (74, 52), (70, 60)], width=1.4, strength=0.9),
    )
    highway = union(mass_pike, ramps)
    river_road_allston = polyline(
        [(47, 0), (42, 8), (36, 16), (30, 22), (20, 23), (11, 25), (9, 30), (10, 34), (16, 35), (24, 40),
         (30, 46), (33, 50), (35, 58), (37, 64), (42, 67), (55, 67), (70, 67), (85, 67), (100, 68)],
        width=1.1, strength=0.75)
    river_road_cambridge = polyline(
        [(6, 40), (12, 46), (18, 51), (23, 56), (24, 62), (25, 68), (30, 74), (40, 77), (60, 77),
         (80, 78), (100, 81)], width=1.1, strength=0.65)
    # Two streets with shops: first draw a line with strength 1, then each use below multiplies by its own strength
    western_ave = polyline([(57, 0), (55, 25), (53, 50), (52, 70), (51, 76)], width=1.5)
    river_st = polyline([(67, 70), (66, 78), (60, 90), (55, 100)], width=1.3)
    roads = union(highway, river_road_allston, river_road_cambridge, 0.55 * western_ave, 0.5 * river_st)

    return {
        "highway": highway,
        "roads": roads,
        # Historic buildings: Harvard riverside dorms, toward Harvard Square, Harvard Stadium (1903), the old HBS campus
        "historic": union(
            ellipse(14, 55, 12, 13), ellipse(5, 75, 8, 14, 0.9),
            ellipse(38, 37, 4, 4), ellipse(41, 56, 8, 9, 0.8),
        ),
        # Cultural streets: around Harvard Square, Western Ave (Barry's Corner), River St
        "cultural": union(
            ellipse(5, 72, 8, 16), 0.7 * western_ave, 0.6 * river_st,
        ),
        # Modern architecture: the Allston science building (big white building), new glass buildings, Western Ave commercial buildings
        "modern": union(
            ellipse(62, 34, 3, 4), ellipse(59, 57, 4, 5), ellipse(54, 57, 3, 3, 0.8),
            ellipse(55, 8, 5, 9, 0.5),
        ),
        # Public art: the landscaped plaza in front of the science building, Harvard Square
        "art": union(ellipse(60, 40, 3, 4), ellipse(4, 72, 4, 6, 0.6)),
        # Landmarks: Stadium, Weeks footbridge, red-roofed boathouses, a few bridges, Harvard Square
        "landmark": union(
            ellipse(38, 37, 3, 3), ellipse(29, 50, 2, 2, 0.8), ellipse(26, 54, 1.5, 1.5, 0.9),
            ellipse(33, 67, 2, 2, 0.7), ellipse(52, 72, 1.5, 2.5, 0.5), ellipse(67, 73, 1.5, 2.5, 0.5),
            ellipse(14, 15, 2, 2, 0.5), ellipse(4, 72, 3, 4, 0.8),
        ),
        # Gardens / well-kept green space: HBS lawns, the science building plaza, the top-left park
        "gardens": union(ellipse(45, 43, 4, 6, 0.6), ellipse(60, 40, 3, 4, 0.8), ellipse(25, 4, 8, 6, 0.4)),
        # Parks: top-left park, bottom-right riverside park, small field on the right, HBS lawns
        "park": union(ellipse(25, 5, 12, 7), ellipse(96, 86, 5, 8), ellipse(52, 90, 3, 3), ellipse(45, 43, 4, 6)),
        # Campus (lots of people): HBS, Harvard dorms
        "campus": union(ellipse(41, 56, 9, 10), ellipse(14, 55, 12, 13)),
        # Industrial / construction / parking: rail yard, construction sites, empty lots by the interchange, parking lots, big warehouse at the bottom left
        "industrial": union(
            ellipse(93, 45, 8, 24), ellipse(65, 52, 7, 7, 0.8), ellipse(77, 65, 7, 4, 0.7),
            ellipse(52, 55, 2, 6, 0.5), ellipse(94, 5, 4, 5, 0.4),
        ),
        # Commercial (not quiet residential): commercial buildings on both sides of Western Ave
        "commercial": union(ellipse(55, 8, 6, 10), 0.8 * western_ave, 0.6 * river_st),
    }


# ---------------- 5) Combine into 20-dimension scores ----------------
def build_scores() -> dict[str, np.ndarray]:
    img = load_square_image()
    f = classify_pixels(img)
    tex = texture(img)
    a = annotations()

    water = keep_river(f["water"])
    green = f["grass"] + f["tree"]
    built_near = blur(f["built"], 2)
    mix = np.clip(2 * np.minimum(f["built"], green), 0, 1)  # houses mixed with greenery: a typical neighborhood
    river_band = blur(water, 2)
    bike_band = np.clip(1 - np.abs(river_band - 0.35) / 0.3, 0, 1) * (water < 0.5)  # the strip along the riverbank
    rng = np.random.default_rng(SEED)

    # Each dimension = color shares + labels, computed directly on 0-1
    s = {
        # Water: the tile itself is water, or water can be seen within 2 tiles
        "water": np.maximum(water, 0.9 * river_band),
        # Tree canopy: share of dark green nearby
        "tree_canopy": 1.4 * blur(f["tree"], 1),
        # Park: grass + trees, minus dense buildings, plus the labeled parks
        "park": 0.9 * blur(f["grass"], 1) + 0.5 * blur(f["tree"], 1) * (1 - built_near) + 0.45 * a["park"],
        # Open field: lots of grass, track or sand, and flat texture (sports fields, big lawns)
        "open_field": 1.5 * blur(f["grass"] + f["track"] + 0.5 * f["bare"], 2) * (1 - 0.7 * tex),
        # Farmland: none in the city, just a bit of noise
        "farmland": 0.03 * rng.random((GRID, GRID)),
        # Forest: large continuous trees (very high tree share within 3 tiles)
        "forest": 2.2 * (blur(f["tree"], 3) - 0.35),
        # Hills: this area is very flat
        "hills": 0.04 + 0.03 * rng.random((GRID, GRID)),
        # Sky view: the fewer buildings around, the more open; most open over the river
        "sky_view": 0.95 - 0.9 * blur(f["built"], 3) + 0.3 * river_band,
        # Gardens: labeled well-kept green space + small patches of green in neighborhood yards
        "gardens": 0.75 * a["gardens"] + 0.6 * blur(mix, 1),
        # Historic buildings: inside the labeled areas, places with buildings score high
        "historic_buildings": a["historic"] * (0.35 + 0.9 * blur(f["built"], 1)),
        # Cultural street: inside the labeled areas, the denser the buildings, the livelier
        "cultural_street": a["cultural"] * (0.35 + 0.8 * blur(f["built"], 1)),
        # Modern architecture: labeled areas, big white roofs get extra points
        "modern_architecture": a["modern"] * (0.55 + 0.8 * blur(f["white"], 1)),
        # Public art: labels only
        "public_art": 0.8 * a["art"],
        # Landmark: labels only
        "landmark": a["landmark"],
        # Quiet residential: houses + greenery mixed, busy texture, minus commercial, industrial, main roads
        "quiet_residential": (0.25 + 1.1 * blur(mix, 1)) * tex
                             * (1 - a["commercial"]) * (1 - a["industrial"]) * (1 - 0.85 * a["roads"]) * (water < 0.5),
        # Industrial: sand and built-up areas inside the labeled areas
        "industrial": a["industrial"] * (0.35 + 0.8 * blur(f["bare"] + f["built"], 1)),
        # Traffic: main roads + background noise from small neighborhood streets
        "traffic": np.maximum(a["roads"], 0.12 * f["built"] / max(f["built"].max(), 1e-6)),
        # Bike lane: riverside paths on both banks (Paul Dudley White bike path), broken near the interchange
        "bike_lane": 0.85 * bike_band * (1 - a["highway"]),
        # Crowds: cultural streets, campus, Stadium, riverside paths
        "crowds": 0.8 * a["cultural"] + 0.45 * a["campus"] + 0.3 * ellipse(38, 37, 4, 4)
                  + 0.25 * bike_band + 0.12 * mix,
        # Cleanliness: green space and waterfront get points; industrial and highways lose points
        "cleanliness": 0.62 + 0.2 * blur(green, 2) + 0.1 * river_band - 0.5 * a["industrial"] - 0.3 * a["highway"],
    }

    # Check: the dimensions from the formulas must match dimensions.json exactly, not one more or less; otherwise tell you to fix the formulas
    if set(s) != set(DIMENSION_NAMES):
        missing = sorted(set(DIMENSION_NAMES) - set(s))
        extra = sorted(set(s) - set(DIMENSION_NAMES))
        raise SystemExit(f"dimensions.json 和脚本里的公式对不上：缺公式 {missing}，多余公式 {extra}")

    # Same for all: add a bit of noise (imperfect like a real model) → clip to 0-1, keep 3 decimals; output in the order of dimensions.json
    return {
        name: np.round(np.clip(s[name] + rng.normal(0, 0.02, s[name].shape), 0, 1), 3)
        for name in DIMENSION_NAMES
    }


# ---------------- 6) Preview image: draw a small heatmap for each of the 20 dimensions, next to the original image, for people to check ----------------
def save_preview(scores: dict[str, np.ndarray]) -> None:
    cell, pad, cols = 200, 22, 7
    names = ["satellite"] + list(scores)
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + pad)), "white")
    draw = ImageDraw.Draw(sheet)
    sat = Image.fromarray(load_square_image().astype(np.uint8)).resize((cell, cell))
    for i, name in enumerate(names):
        x, y = (i % cols) * cell, (i // cols) * (cell + pad)
        if name == "satellite":
            tile = sat
        else:
            v = scores[name]  # scores are already 0-1: 0 = white, 1 = dark green
            rgb = np.stack([255 - 230 * v, 255 - 130 * v, 255 - 200 * v], axis=-1).astype(np.uint8)
            tile = Image.fromarray(rgb).resize((cell, cell), Image.NEAREST)
        sheet.paste(tile, (x, y + pad))
        draw.text((x + 4, y + 5), name, fill="black")
    sheet.save(OUT_PREVIEW)


# Main flow: compute scores → save npz → save preview image
if __name__ == "__main__":
    scores = build_scores()
    np.savez_compressed(OUT_SCORES, **scores)
    save_preview(scores)
    for name, v in scores.items():
        print(f"{name:20s} mean {v.mean():.3f}  max {v.max():.3f}")
    print("saved", OUT_SCORES.name, OUT_PREVIEW.name)
