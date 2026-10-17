'use client'

// Main page: three columns, the page doesn't scroll — left nav | middle heatmap + legend + input box | right pipeline inspector (shows the data automatically).
import { useEffect, useRef, useState } from 'react';
import DataService from '@/services/DataService';
import Sidebar from '@/components/layout/Sidebar';
import Heatmap from '@/components/heatmap/Heatmap';
import Legend from '@/components/heatmap/Legend';
import Inspector, { signed } from '@/components/pipeline/Inspector';

// A few example sentences; click one to submit it right away
const EXAMPLES = [
    'Ride along the river, shade if possible, avoid traffic',
    'Streets with a cultural, historic feel',
    'A quiet, relaxing loop',
    'Water is a must, nothing else matters',
];

// Width of the legend column + gap (subtract it when working out how big the heatmap can be)
const LEGEND_SPACE = 150;

// Measure how big the middle area is and work out the heatmap's side length: take the smaller of width and height, so no scrollbar shows up
function useFitSquare() {
    const ref = useRef(null);
    const [size, setSize] = useState(480);
    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        const observer = new ResizeObserver(([entry]) => {
            const { width, height } = entry.contentRect;
            setSize(Math.max(200, Math.floor(Math.min(width - LEGEND_SPACE, height))));
        });
        observer.observe(el);
        return () => observer.disconnect();
    }, []);
    return [ref, size];
}

export default function HomePage() {
    const [text, setText] = useState('');
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [mapRef, mapSize] = useFitSquare();

    // Submit: call /score, get back the heatmap + the intermediate results of every step
    async function submit(value = text) {
        const query = value.trim();
        if (!query || loading) return;
        setLoading(true);
        setError(null);
        try {
            const resp = await DataService.Score(query);
            setResult(resp.data);
        } catch (e) {
            setError(e.response?.data?.detail ? JSON.stringify(e.response.data.detail) : e.message);
        } finally {
            setLoading(false);
        }
    }

    // Press Enter to submit; an Enter pressed while the input method is still picking characters doesn't count
    function onKeyDown(e) {
        if (e.key === 'Enter' && !e.nativeEvent.isComposing) submit();
    }

    // Values at the two ends of the legend: take the bigger absolute value of the positive and negative sides, so 0 (region average) is always in the middle
    const maxAbs = result
        ? Math.max(Math.abs(result.stats.t_min ?? 0), Math.abs(result.stats.t_max ?? 0)) || 1
        : 1;
    const topWeights = result
        ? [...result.agent.preference.weights].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight)).slice(0, 5)
        : [];

    return (
        <div className="flex h-screen overflow-hidden bg-[#f6f5fa] text-slate-900">
            <Sidebar />

            {/* Middle work area */}
            <main className="flex min-w-0 flex-1 flex-col">
                {/* Top bar: breadcrumb + region info */}
                <header className="flex h-14 shrink-0 items-center gap-2 border-b border-slate-200 bg-white/[0.7] px-6 text-sm">
                    <span className="text-slate-500">Regions</span>
                    <span className="text-slate-300">›</span>
                    <span className="shrink-0 font-medium text-slate-900">Charles River · Harvard / Allston</span>
                    <span className="ml-2 shrink-0 rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700">demo · 100 × 100 tiles</span>
                    <span className="ml-auto min-w-0 truncate pl-3 text-xs text-slate-500">Model scores are simulated (M2)</span>
                </header>

                {/* Heatmap + legend: fit the remaining space automatically */}
                <div ref={mapRef} className="flex min-h-0 flex-1 items-center justify-center gap-6 px-6 py-5">
                    <Heatmap heatmap={result?.heatmap} maxAbs={maxAbs} loading={loading} size={mapSize} />
                    <div className="self-end pb-2">
                        <Legend maxAbs={maxAbs} hasResult={!!result} />
                    </div>
                </div>

                {/* Prompt input */}
                <div className="shrink-0 border-t border-slate-200 bg-white px-6 py-4">
                    <div className="flex gap-2">
                        <input
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                            onKeyDown={onKeyDown}
                            placeholder="Describe the scenery you want to ride through, then press Enter"
                            className="h-12 min-w-0 flex-1 rounded-xl border border-slate-300 bg-white px-4 text-base outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-200"
                        />
                        <button
                            onClick={() => submit()}
                            disabled={loading || !text.trim()}
                            className="h-12 rounded-xl bg-gradient-to-r from-violet-600 to-pink-500 px-6 font-medium text-white shadow-sm transition hover:brightness-105 disabled:opacity-40"
                        >
                            {loading ? 'Scoring…' : 'Generate'}
                        </button>
                    </div>

                    {/* Example sentences / the preference that was read / errors */}
                    <div className="mt-3 flex h-7 items-center gap-2 overflow-hidden text-xs">
                        {error ? (
                            <span className="truncate rounded-md bg-red-50 px-2 py-1 text-red-800">Request failed: {error}</span>
                        ) : result ? (
                            <>
                                <span className="shrink-0 text-slate-500">Parsed:</span>
                                {topWeights.map((w) => (
                                    <span key={w.dimension} title={w.reason} className={`shrink-0 rounded-full px-2.5 py-1 font-mono ${w.weight > 0 ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>
                                        {w.dimension} {signed(w.weight)}
                                    </span>
                                ))}
                                <span className="shrink-0 text-slate-400">· details on the right →</span>
                            </>
                        ) : (
                            <>
                                <span className="shrink-0 text-slate-500">Try:</span>
                                {EXAMPLES.map((ex) => (
                                    <button
                                        key={ex}
                                        onClick={() => { setText(ex); submit(ex); }}
                                        className="shrink-0 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-slate-700 transition hover:border-violet-300 hover:bg-violet-50"
                                    >
                                        {ex}
                                    </button>
                                ))}
                            </>
                        )}
                    </div>
                </div>
            </main>

            <Inspector result={result} loading={loading} />
        </div>
    );
}
