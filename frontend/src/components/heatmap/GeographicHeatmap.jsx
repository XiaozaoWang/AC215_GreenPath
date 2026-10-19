'use client';
import { useMemo, useState } from 'react';
import { colorFor, rgb } from './colors';

// GeoJSON [longitude, latitude]. A local equirectangular projection preserves the
// relative positions of the Boston cells; no fake row/column assignment is used.
export default function GeographicHeatmap({ features, maxAbs, size, loading, dimensions = {} }) {
    const [hover, setHover] = useState(null);
    const shapes = useMemo(() => {
        const positions = features.flatMap(f => f.geometry.coordinates.flat());
        if (!positions.length) return [];
        const lat0 = positions.reduce((s, p) => s + p[1], 0) / positions.length;
        const cos = Math.cos(lat0 * Math.PI / 180);
        const project = ([lon, lat]) => [lon * cos, -lat];
        const points = positions.map(project);
        const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
        const x0 = Math.min(...xs), y0 = Math.min(...ys);
        const dx = Math.max(...xs) - x0, dy = Math.max(...ys) - y0;
        const scale = 950 / Math.max(dx, dy, 1e-9);
        const xpad = (1000 - dx * scale) / 2, ypad = (1000 - dy * scale) / 2;
        return features.map(f => ({ ...f, path: f.geometry.coordinates.map(ring => ring.map((p, i) => {
            const [x, y] = project(p);
            return `${i ? 'L' : 'M'}${((x - x0) * scale + xpad).toFixed(2)},${((y - y0) * scale + ypad).toFixed(2)}`;
        }).join(' ') + ' Z').join(' ') }));
    }, [features]);
    const label = id => dimensions[id]?.label || id;
    return (
        <div className="relative rounded-xl border border-slate-200 bg-white shadow-sm" style={{ width: size, height: size }}>
            <svg viewBox="0 0 1000 1000" className="h-full w-full" role="img" aria-label="Geographic heatmap of real H3 cells" style={{ opacity: loading ? .4 : 1 }} onMouseLeave={() => setHover(null)}>
                {shapes.map(f => <path key={f.id} d={f.path} fill={rgb(colorFor(f.properties.value, maxAbs))} fillRule="evenodd" stroke="#64748b" strokeWidth=".7" onMouseEnter={() => setHover(f)}>
                    <title>{f.id}: {f.properties.value ?? 'excluded'}</title>
                </path>)}
            </svg>
            <div className="absolute left-3 top-3 rounded bg-white/90 px-2 py-1 text-xs text-slate-600">N ↑ · gaps mean no supplied prediction</div>
            {hover && <div className="pointer-events-none absolute bottom-3 left-3 right-3 rounded bg-slate-900/95 px-3 py-2 text-xs text-white">
                <div className="break-all font-mono">{hover.properties.cell_id}</div>
                <div>Preference score: {hover.properties.value ?? 'excluded by requirement'}</div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">{Object.entries(hover.properties.scores).map(([id, value]) => <span key={id}>{label(id)}: {value.toFixed(2)}</span>)}</div>
            </div>}
            {loading && <div className="absolute inset-0 flex items-center justify-center text-xl text-slate-700">Scoring…</div>}
        </div>
    );
}
