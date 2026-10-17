'use client'

// Heatmap: draw the rows×cols total scores t as a square image; hovering the mouse shows this tile's row/col and score.
// size is worked out outside (in pixels), so the page never gets a vertical scrollbar.
import { useEffect, useRef, useState } from 'react';
import { colorFor } from './colors';

export default function Heatmap({ heatmap, maxAbs, loading, size }) {
    const canvasRef = useRef(null);
    const [hover, setHover] = useState(null);

    // Draw: one tile = one pixel, then CSS scales it up to the box size (pixelated keeps the tile edges sharp)
    useEffect(() => {
        if (!heatmap || !canvasRef.current) return;
        const rows = heatmap.length;
        const cols = heatmap[0].length;
        const canvas = canvasRef.current;
        canvas.width = cols;
        canvas.height = rows;
        const ctx = canvas.getContext('2d');
        const img = ctx.createImageData(cols, rows);
        heatmap.forEach((row, r) => row.forEach((t, c) => {
            const [R, G, B] = colorFor(t, maxAbs);
            const i = (r * cols + c) * 4;
            img.data[i] = R;
            img.data[i + 1] = G;
            img.data[i + 2] = B;
            img.data[i + 3] = 255;
        }));
        ctx.putImageData(img, 0, 0);
    }, [heatmap, maxAbs]);

    // Mouse position → which row and column → this tile's score
    function onMove(e) {
        const box = e.currentTarget.getBoundingClientRect();
        const x = e.clientX - box.left;
        const y = e.clientY - box.top;
        const row = Math.min(heatmap.length - 1, Math.floor((y / box.height) * heatmap.length));
        const col = Math.min(heatmap[0].length - 1, Math.floor((x / box.width) * heatmap[0].length));
        setHover({ x, y, row, col, t: heatmap[row][col] });
    }

    return (
        <div
            className="relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
            style={{ width: size, height: size }}
        >
            {/* No result yet: placeholder */}
            {!heatmap && (
                <div className="flex h-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-violet-50 via-white to-pink-50 text-center">
                    <div className="text-2xl font-semibold text-slate-400">{loading ? 'Scoring…' : 'Scenery heatmap'}</div>
                    {!loading && <div className="max-w-xs text-sm text-slate-400">Describe the scenery you want below and press Enter.</div>}
                </div>
            )}

            {/* Has a result: canvas + hover tooltip */}
            {heatmap && (
                <>
                    <canvas
                        ref={canvasRef}
                        className="block h-full w-full cursor-crosshair"
                        style={{ imageRendering: 'pixelated', opacity: loading ? 0.35 : 1 }}
                        onMouseMove={onMove}
                        onMouseLeave={() => setHover(null)}
                    />
                    {hover && (
                        <div
                            className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 font-mono text-xs text-white shadow"
                            style={{ left: Math.min(hover.x + 12, size - 130), top: Math.min(hover.y + 12, size - 44) }}
                        >
                            row {hover.row} · col {hover.col}<br />
                            t = {hover.t === null ? 'below must-have gate' : hover.t.toFixed(3)}
                        </div>
                    )}
                    {loading && (
                        <div className="absolute inset-0 flex items-center justify-center text-2xl font-semibold text-slate-700">Scoring…</div>
                    )}
                </>
            )}
        </div>
    );
}
