// Heatmap colors: 0 = area average (near white), more teal = fits the preference better, more coral red = fits worse, failed the must-have threshold = light purple gray
export const NEG = [226, 92, 76];

// Route line colours by route kind (also used by the route cards): the shortest route is dark, the options warm up
// from amber to red as they follow the preference more closely. They contrast with the purple-to-yellow cell colours.
export const ROUTE_COLORS = { shortest: '#1d1d1f', efficient: '#ffb300', balanced: '#ff6a00', preferred: '#e5391a' };
export const MID = [248, 247, 251];
export const POS = [16, 150, 120];
export const EXCLUDED = [214, 211, 224];

// Turn one score into one RGB color. maxAbs is the score that gets the darkest teal / darkest coral red
export function colorFor(t, maxAbs) {
    if (t === null || t === undefined) return EXCLUDED;
    const x = Math.max(-1, Math.min(1, t / maxAbs));
    const end = x >= 0 ? POS : NEG;
    return MID.map((m, i) => Math.round(m + (end[i] - m) * Math.abs(x)));
}

// RGB array to CSS color string
export function rgb([r, g, b]) {
    return `rgb(${r}, ${g}, ${b})`;
}
