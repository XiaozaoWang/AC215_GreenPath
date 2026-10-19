// Heatmap colors: 0 = region average (near-white); teal = better match; coral red = worse;
// failed the must-have gate = light purple-gray; no model prediction in that cell = gray.
export const NEG = [226, 92, 76];
export const MID = [248, 247, 251];
export const POS = [16, 150, 120];
export const EXCLUDED = [214, 211, 224];
export const NO_DATA = [128, 128, 128];

// One score → one RGB color; maxAbs is the score drawn at full teal / full coral
export function colorFor(t, maxAbs) {
    if (t === null || t === undefined) return EXCLUDED;
    const x = Math.max(-1, Math.min(1, t / maxAbs));
    const end = x >= 0 ? POS : NEG;
    return MID.map((m, i) => Math.round(m + (end[i] - m) * Math.abs(x)));
}

// RGB array → CSS color string
export function rgb([r, g, b]) {
    return `rgb(${r}, ${g}, ${b})`;
}
