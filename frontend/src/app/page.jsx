'use client'

// Main page: three columns, the page doesn't scroll — left nav | middle heatmap + legend + input box | right pipeline inspector (shows the data automatically).
import { useEffect, useRef, useState } from 'react';
import DataService from '@/services/DataService';
import Sidebar from '@/components/layout/Sidebar';
import Heatmap from '@/components/heatmap/Heatmap';
import GeographicHeatmap from '@/components/heatmap/GeographicHeatmap';
import Legend from '@/components/heatmap/Legend';
import Inspector, { signed } from '@/components/pipeline/Inspector';

// A few example sentences; click one to submit it right away


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
            setSize(Math.max(200, Math.floor(Math.min(width - (window.innerWidth >= 768 ? LEGEND_SPACE : 0), height))));
        });
        observer.observe(el);
        return () => observer.disconnect();
    }, []);
    return [ref, size];
}

export default function HomePage() {
    const [text, setText] = useState('');
    const [capabilities, setCapabilities] = useState(null);
    useEffect(() => {
        DataService.Capabilities().then(r => setCapabilities(r.data)).catch(() => setError('Could not load the model contract. Check API startup and artifact verification.'));
    }, []);
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [mapRef, mapSize] = useFitSquare();

    // Submit: call /score, get back the heatmap + the intermediate results of every step
    async function submit(value = text) {
        const query = value.trim();
        if (!query || loading || !capabilities) return;
        setLoading(true);
        setError(null);
        try {
            const resp = await DataService.Score(query, capabilities.default_region_id);
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
        <div className="flex min-h-screen flex-col overflow-x-hidden bg-[#f6f5fa] xl:h-screen xl:flex-row xl:overflow-hidden text-slate-900">
            <Sidebar capabilities={capabilities} />

            {/* Middle work area */}
            <main className="flex min-h-[680px] min-w-0 flex-1 flex-col xl:min-h-0">
                {/* Top bar: breadcrumb + region info */}
                <header className="flex min-h-14 shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 bg-white/[0.7] px-4 py-3 text-sm">
                    <span className="text-slate-500">Regions</span>
                    <span className="text-slate-300">›</span>
                    <span className="shrink-0 font-medium text-slate-900">{capabilities?.regions[0]?.label || 'Loading model contract…'}</span>

                    <span className="ml-auto min-w-0 truncate pl-3 text-xs text-slate-500">{capabilities ? `${Object.keys(capabilities.dimensions).length} model dimensions` : ''}</span>
                </header>

                {/* Heatmap + legend: fit the remaining space automatically */}
                <div ref={mapRef} className="flex min-h-[400px] flex-1 items-center justify-center gap-4 px-4 py-5 xl:min-h-0">
                    <div className="flex flex-col items-center gap-2">
                        {result?.features?.length ? <GeographicHeatmap features={result.features} dimensions={capabilities?.dimensions} maxAbs={maxAbs} loading={loading} size={mapSize} /> : <Heatmap heatmap={result?.heatmap} maxAbs={maxAbs} loading={loading} size={mapSize} />}
                        <p className="max-w-xl text-center text-xs text-slate-500">{capabilities?.description}</p><p className="text-xs text-slate-500 md:hidden">Teal: better match · coral: worse · gray: excluded</p>
                        {!!result?.agent.preference.unsupported?.length && <p role="status" className="max-w-xl text-center text-sm text-amber-800">Not scored by this model: {result.agent.preference.unsupported.join(', ')}. These preferences are not enforced.</p>}
                    </div>
                    <div className="hidden self-end pb-2 md:block">
                        <Legend maxAbs={maxAbs} hasResult={!!result} />
                    </div>
                </div>

                {/* Prompt input */}
                <div className="shrink-0 border-t border-slate-200 bg-white px-4 py-4">
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
                            disabled={loading || !text.trim() || !capabilities}
                            className="h-12 rounded-xl bg-gradient-to-r from-violet-600 to-pink-500 px-6 font-medium text-white shadow-sm transition hover:brightness-105 disabled:opacity-40"
                        >
                            {loading ? 'Scoring…' : 'Generate'}
                        </button>
                    </div>

                    {/* Example sentences / the preference that was read / errors */}
                    <div className="mt-3 flex min-h-7 flex-wrap items-center gap-2 text-xs">
                        {error ? (
                            <span className="truncate rounded-md bg-red-50 px-2 py-1 text-red-800">Request failed: {error}</span>
                        ) : result ? (
                            <>
                                <span className="shrink-0 text-slate-500">Parsed:</span>
                                {topWeights.map((w) => (
                                    <span key={w.dimension} title={w.reason} className={`shrink-0 rounded-full px-2.5 py-1 font-mono ${w.weight > 0 ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>
                                        {capabilities?.dimensions[w.dimension]?.label || w.dimension} {signed(w.weight)}
                                    </span>
                                ))}
                                <span className="shrink-0 text-slate-400">· details on the right →</span>
                            </>
                        ) : (
                            <>
                                <span className="shrink-0 text-slate-500">Try:</span>
                                {(capabilities?.examples || []).map((ex) => (
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

            <Inspector result={result} loading={loading} capabilities={capabilities} />
        </div>
    );
}
