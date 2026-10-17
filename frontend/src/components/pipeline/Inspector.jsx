'use client'

// Right-side "pipeline inspector": every intermediate result returned by /score lives here, shown in tabs; only the inside of this panel can scroll.
import { useEffect, useState } from 'react';
import DataService from '@/services/DataService';

// Pipeline steps (numbers match the flow diagram ①–⑩), used as an explanation when there's no result
const STEPS = [
    { n: '①', title: 'Your words', detail: 'Text from the prompt box', color: 'bg-violet-500' },
    { n: '②–⑤', title: 'Harness · Gemini', detail: 'Parse weights, validate with pydantic, retry with the error', color: 'bg-pink-500' },
    { n: '⑥⑦', title: 'Tiles', detail: '100 × 100 grid over the region', color: 'bg-teal-500' },
    { n: '⑧⑨', title: 'Model', detail: '20 raw scores per tile (M2: simulated from the satellite image)', color: 'bg-amber-500' },
    { n: '⑩', title: 'Score math', detail: 'z-score per region → t = Z · w → heatmap', color: 'bg-slate-700' },
];

const TABS = ['Overview', 'Harness', 'Weights', 'Stats', 'JSON', 'Code'];

// Show the sign of a weight: +1.0 / −0.6
export function signed(v) {
    return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1);
}

export default function Inspector({ result, loading }) {
    const [tab, setTab] = useState('Overview');
    const total = result ? Object.values(result.timings_ms).reduce((a, b) => a + b, 0) : 0;

    return (
        <aside className="flex h-full w-[400px] shrink-0 flex-col border-l border-slate-200 bg-white">
            {/* Title + status */}
            <div className="flex items-center gap-2 px-5 pb-3 pt-5">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-pink-400 text-xs font-bold text-white">i</span>
                <h2 className="text-base font-semibold text-slate-900">Pipeline inspector</h2>
                <span className={`ml-auto rounded-full px-2.5 py-0.5 text-xs ${loading ? 'bg-amber-100 text-amber-800' : result ? 'bg-emerald-50 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                    {loading ? 'Running…' : result ? `Done · ${total.toFixed(0)} ms` : 'Idle'}
                </span>
            </div>

            {/* Tabs (Code can be viewed without a result) */}
            <div className="flex border-b border-slate-200 px-3">
                {TABS.map((t) => {
                    const disabled = !result && t !== 'Overview' && t !== 'Code';
                    return (
                        <button
                            key={t}
                            disabled={disabled}
                            onClick={() => setTab(t)}
                            className={`-mb-px border-b-2 px-2 py-2 text-[13px] ${tab === t ? 'border-violet-500 font-medium text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-800'} disabled:cursor-default disabled:text-slate-300`}
                        >
                            {t}
                        </button>
                    );
                })}
            </div>

            {/* Content area: only this part scrolls */}
            <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-5 py-4">
                {tab === 'Overview' && (result ? <Overview result={result} /> : <HowItWorks />)}
                {tab === 'Harness' && result && <Harness agent={result.agent} />}
                {tab === 'Weights' && result && <Weights weights={result.weights} preference={result.agent.preference} />}
                {tab === 'Stats' && result && <Stats stats={result.stats} weights={result.weights} />}
                {tab === 'JSON' && result && <RawJson result={result} />}
                {tab === 'Code' && <SourceCode />}
            </div>
        </aside>
    );
}

// ---------- small parts ----------
// Small heading
function Section({ title, children, right }) {
    return (
        <section className="mb-5">
            <div className="mb-2 flex items-baseline">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
                {right && <span className="ml-auto text-xs text-slate-400">{right}</span>}
            </div>
            {children}
        </section>
    );
}

// Code block
function Code({ children, tone = 'plain' }) {
    const toneClass = tone === 'error' ? 'bg-red-50 text-red-800' : 'bg-slate-50 text-slate-800';
    return <pre className={`overflow-x-auto whitespace-pre-wrap break-words rounded-lg p-3 font-mono text-[11px] leading-relaxed ${toneClass}`}>{children}</pre>;
}

// A small number card
function Stat({ label, value }) {
    return (
        <div className="rounded-lg border border-slate-200 px-3 py-2">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="font-mono text-sm text-slate-900">{value}</div>
        </div>
    );
}

// ---------- the tabs ----------
// When there's no result: pipeline explanation (like the colored status bar on the right side of eggplant)
function HowItWorks() {
    return (
        <>
            <p className="mb-4 text-sm text-slate-600">Each request runs these steps. Results appear here automatically.</p>
            <div className="flex flex-col gap-2">
                {STEPS.map((s) => (
                    <div key={s.n} className="flex items-start gap-3 rounded-lg border border-slate-200 p-3">
                        <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] text-white ${s.color}`}>{s.n}</span>
                        <div className="min-w-0">
                            <div className="text-sm font-medium text-slate-900">{s.title}</div>
                            <div className="text-xs text-slate-500">{s.detail}</div>
                        </div>
                    </div>
                ))}
            </div>
        </>
    );
}

// Overview: the preference that was read (with reasons) + key numbers + time per step
function Overview({ result }) {
    const weights = [...result.agent.preference.weights].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
    const labels = { agent: '②–⑤ Harness (Gemini)', tiles: '⑥⑦ Tiles', model: '⑧⑨ Model (simulated)', score: '⑩ Score math' };
    const total = Object.values(result.timings_ms).reduce((a, b) => a + b, 0);
    return (
        <>
            <Section title="Parsed preference" right={`${weights.length} dimensions`}>
                {weights.length === 0 && <div className="text-sm text-slate-500">No preference found (all weights 0).</div>}
                <div className="flex flex-col gap-1.5">
                    {weights.map((w) => (
                        <div key={w.dimension} className="flex items-start gap-2 text-sm">
                            <span className={`w-12 shrink-0 rounded px-1.5 text-center font-mono text-xs leading-5 ${w.weight > 0 ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>{signed(w.weight)}</span>
                            <div className="min-w-0">
                                <div className="font-mono text-slate-900">{w.dimension}</div>
                                <div className="text-xs text-slate-500">{w.reason}</div>
                            </div>
                        </div>
                    ))}
                </div>
                {result.agent.preference.must_have.length > 0 && (
                    <div className="mt-2 text-xs text-slate-600">Must have: <span className="font-mono">{result.agent.preference.must_have.join(', ')}</span></div>
                )}
            </Section>

            <Section title="Summary">
                <div className="grid grid-cols-2 gap-2">
                    <Stat label="LLM attempts" value={`${result.agent.attempts.length}${result.agent.used_fallback ? ' · fallback' : ''}`} />
                    <Stat label="Tiles" value={`${result.rows} × ${result.cols}`} />
                    <Stat label="t range" value={`${result.stats.t_min} … ${result.stats.t_max}`} />
                    <Stat label="Below must-have" value={result.stats.n_excluded} />
                </div>
            </Section>

            <Section title="Timing" right={`${total.toFixed(0)} ms total`}>
                <div className="flex flex-col gap-1.5">
                    {Object.entries(result.timings_ms).map(([name, ms]) => (
                        <div key={name} className="grid grid-cols-[9.5rem_1fr_4rem] items-center gap-2 text-xs">
                            <span className="text-slate-700">{labels[name] || name}</span>
                            <div className="h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-gradient-to-r from-violet-500 to-pink-400" style={{ width: `${(ms / total) * 100}%` }} /></div>
                            <span className="text-right font-mono text-slate-600">{ms} ms</span>
                        </div>
                    ))}
                </div>
            </Section>
        </>
    );
}

// ②–⑤ harness: each time we ask Gemini, what was sent, what came back, and why it was rejected
function Harness({ agent }) {
    return (
        <>
            <p className="mb-4 text-sm text-slate-600">
                Every round trip to Gemini (<span className="font-mono">agent.py</span>). Rejected answers are sent back with the validation error.
            </p>
            {agent.attempts.map((a, i) => (
                <Section key={i} title={`Attempt ${i + 1}`} right={a.error ? '✗ rejected' : '✓ passed pydantic'}>
                    <div className="mb-1 text-xs text-slate-500">Prompt sent (system instruction: see Code → prompts.py)</div>
                    <Code>{a.prompt}</Code>
                    <div className="mb-1 mt-2 text-xs text-slate-500">Raw reply</div>
                    <Code>{a.raw || '(empty)'}</Code>
                    {a.error && (<><div className="mb-1 mt-2 text-xs text-slate-500">Validation error</div><Code tone="error">{a.error}</Code></>)}
                </Section>
            ))}
            {agent.used_fallback && <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Budget used up: fell back to all-zero weights.</div>}
        </>
    );
}

// Full weight vector w: list all 20 dimensions, 0 for the ones not mentioned
function Weights({ weights, preference }) {
    const reasons = Object.fromEntries(preference.weights.map((w) => [w.dimension, w.reason]));
    return (
        <>
            <p className="mb-4 text-sm text-slate-600">Full weight vector <span className="font-mono">w</span> (all 20 dimensions; unmentioned = 0).</p>
            <div className="flex flex-col gap-1.5">
                {Object.entries(weights).map(([name, v]) => (
                    <div key={name} title={reasons[name] || ''} className="grid grid-cols-[8.5rem_1fr_2.5rem] items-center gap-2 text-xs">
                        <span className={`font-mono ${v !== 0 ? 'text-slate-900' : 'text-slate-400'}`}>{name}</span>
                        {/* Bar with the center line in the middle: green to the right, red to the left */}
                        <div className="relative h-2 rounded-full bg-slate-100">
                            <div className="absolute left-1/2 top-[-2px] h-3 w-px bg-slate-300" />
                            <div
                                className={`absolute top-0 h-2 rounded-full ${v >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`}
                                style={{ left: v >= 0 ? '50%' : `${50 + v * 50}%`, width: `${Math.abs(v) * 50}%` }}
                            />
                        </div>
                        <span className="text-right font-mono text-slate-600">{signed(v)}</span>
                    </div>
                ))}
            </div>
        </>
    );
}

// ⑩ Scoring stats: region mean μ, σ (with floor) and weight for each dimension
function Stats({ stats, weights }) {
    return (
        <>
            <p className="mb-3 text-sm text-slate-600">
                <span className="font-mono">Z = clip((X − μ) / σ, −3, 3)</span>, σ = max(region std, model RMSE); <span className="font-mono">t = Z · w</span>.
            </p>
            <table className="w-full text-left font-mono text-xs">
                <thead className="text-slate-500"><tr><th className="py-1.5 font-normal">dimension</th><th className="font-normal">μ</th><th className="font-normal">σ</th><th className="font-normal">w</th></tr></thead>
                <tbody>
                    {Object.keys(stats.mean).map((name) => (
                        <tr key={name} className={`border-t border-slate-100 ${weights[name] !== 0 ? 'text-slate-900' : 'text-slate-400'}`}>
                            <td className="py-1">{name}</td>
                            <td>{stats.mean[name].toFixed(1)}</td>
                            <td>{stats.sigma[name].toFixed(1)}</td>
                            <td>{signed(weights[name])}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </>
    );
}

// The full returned JSON (heatmap is too long, so only the top-left corner is kept as a sample)
function RawJson({ result }) {
    const shown = { ...result, heatmap: [...result.heatmap.slice(0, 2).map((r) => r.slice(0, 6)), `… ${result.rows} rows × ${result.cols} cols (top-left corner shown)`] };
    return (
        <>
            <p className="mb-3 text-sm text-slate-600">Full <span className="font-mono">/score</span> response (<span className="font-mono">ScoreResponse</span> in schemas.py).</p>
            <Code>{JSON.stringify(shown, null, 2)}</Code>
        </>
    );
}

// Backend code: get the file list the first time it opens; pick a file to show its source code
function SourceCode() {
    const [files, setFiles] = useState([]);
    const [current, setCurrent] = useState(null);
    const [code, setCode] = useState('');

    // Get the file list (only once)
    useEffect(() => {
        DataService.SourceList().then((resp) => {
            setFiles(resp.data.files);
            setCurrent(resp.data.files.includes('routers/score.py') ? 'routers/score.py' : resp.data.files[0]);
        }).catch(() => setCode('Could not reach the API.'));
    }, []);

    // When the selected file changes, go get its code
    useEffect(() => {
        if (!current) return;
        DataService.Source(current).then((resp) => setCode(resp.data.code));
    }, [current]);

    return (
        <>
            <p className="mb-3 text-sm text-slate-600">The backend code that is running right now (<span className="font-mono">api-service/api/</span>).</p>
            <div className="mb-3 flex flex-wrap gap-1.5">
                {files.map((f) => (
                    <button
                        key={f}
                        onClick={() => setCurrent(f)}
                        className={`rounded-md px-2 py-1 font-mono text-[11px] ${f === current ? 'bg-violet-600 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}
                    >
                        {f}
                    </button>
                ))}
            </div>
            {code && <Code>{code}</Code>}
        </>
    );
}
