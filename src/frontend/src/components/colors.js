// 热图配色：0 = 区域平均（近白）；越青绿 = 越符合偏好；越珊瑚红 = 越不符合；没过 must-have 门槛 = 浅紫灰
export const NEG = [226, 92, 76];

// Route line colours by route kind (also used by the route cards): the shortest route is dark, the options warm up
// from amber to red as they follow the preference more closely. They contrast with the purple-to-yellow cell colours.
export const ROUTE_COLORS = { shortest: '#1d1d1f', efficient: '#ffb300', balanced: '#ff6a00', preferred: '#e5391a' };
export const MID = [248, 247, 251];
export const POS = [16, 150, 120];
export const EXCLUDED = [214, 211, 224];

// 一个分数 → 一个 RGB 颜色；maxAbs 是画成最深青绿 / 最深珊瑚红的分数
export function colorFor(t, maxAbs) {
    if (t === null || t === undefined) return EXCLUDED;
    const x = Math.max(-1, Math.min(1, t / maxAbs));
    const end = x >= 0 ? POS : NEG;
    return MID.map((m, i) => Math.round(m + (end[i] - m) * Math.abs(x)));
}

// RGB 数组 → CSS 颜色字符串
export function rgb([r, g, b]) {
    return `rgb(${r}, ${g}, ${b})`;
}
