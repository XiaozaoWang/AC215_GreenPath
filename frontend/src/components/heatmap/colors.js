// Heatmap colors: 0 = region average (near-white); the more teal a positive score, the better it matches the preference; the more coral red a negative score, the worse; didn't pass the gate = gray.

// Three end-point colors (RGB)
export const NEG = [226, 92, 76];
export const MID = [248, 247, 251];
export const POS = [16, 150, 120];
export const EXCLUDED = [214, 211, 224];

// One score → one RGB color; maxAbs is the value at the two ends of the legend; anything beyond it is drawn in the end-point color
export function colorFor(t, maxAbs) {
    if (t === null || t === undefined) return EXCLUDED;
    const x = Math.max(-1, Math.min(1, t / maxAbs));
    const end = x >= 0 ? POS : NEG;
    const k = Math.abs(x);
    return MID.map((m, i) => Math.round(m + (end[i] - m) * k));
}

// RGB array → CSS color string
export function rgb([r, g, b]) {
    return `rgb(${r}, ${g}, ${b})`;
}
