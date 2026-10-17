'use client'

// Legend: a vertical gradient bar, teal on top (best match) → near-white in the middle (region average) → coral red at the bottom (worst match), plus a gray block for "didn't pass the gate".
import { NEG, MID, POS, EXCLUDED, rgb } from './colors';

export default function Legend({ maxAbs, hasResult }) {
    // Number labels: don't show numbers when there's no result yet
    const label = (v) => (hasResult ? (v > 0 ? '+' : '') + v.toFixed(2) : '');

    return (
        <div className="flex flex-col gap-3 text-xs text-slate-600">
            <div className="text-sm font-semibold text-slate-900">Legend</div>
            <div className="text-slate-500">total score t</div>
            <div className="flex gap-2.5">
                {/* Gradient bar */}
                <div
                    className="h-48 w-3.5 rounded-full"
                    style={{ background: `linear-gradient(to bottom, ${rgb(POS)}, ${rgb(MID)}, ${rgb(NEG)})` }}
                />
                {/* Three ticks */}
                <div className="flex h-48 flex-col justify-between">
                    <div><div className="font-mono text-slate-900">{label(maxAbs)}</div>best match</div>
                    <div><div className="font-mono text-slate-900">{hasResult ? '0' : ''}</div>region avg</div>
                    <div><div className="font-mono text-slate-900">{label(-maxAbs)}</div>worst match</div>
                </div>
            </div>
            {/* Didn't pass the must_have gate */}
            <div className="flex items-center gap-2">
                <span className="inline-block h-3 w-3.5 rounded-sm" style={{ background: rgb(EXCLUDED) }} />
                fails must-have
            </div>
        </div>
    );
}
