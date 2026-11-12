'use client';
// The whole page: an iPhone 16 on a white background with the map and one input, and next to it a panel that explains
// how the routes were chosen.
import dynamic from 'next/dynamic';
import { useEffect, useMemo, useRef, useState } from 'react';
import DataService from '@/services/DataService';
import ExplainPanel from '@/components/ExplainPanel';
import RideAssistant from '@/components/RideAssistant';
import RouteCards from '@/components/RouteCards';
import { VIRIDIS_GRADIENT } from '@/components/viridis';

// Leaflet needs the browser's window, so the map is only rendered in the browser
const HexMap = dynamic(() => import('@/components/HexMap'), { ssr: false });

// Outline of the supported area, [[south, west], [north, east]]: the bounding box of the Boston road graph
const AREA = [[42.3309, -71.1224], [42.3815, -71.0522]];
const inArea = ({ lat, lng }) => lat >= AREA[0][0] && lat <= AREA[1][0] && lng >= AREA[0][1] && lng <= AREA[1][1];
const toLatLng = routes => routes.flatMap(r => r.geometry.coordinates.map(([lon, lat]) => [lat, lon]));

export default function Home() {
    const [text, setText] = useState('');
    const [assistantOpen, setAssistantOpen] = useState(false);
    const [assistantReady, setAssistantReady] = useState(false);
    const assistantButton = useRef(null);

    // Tap the map twice to set the start and end; the shortest route appears at once. Then describe the ride.
    // A third tap starts over. phase: idle (picking points) | planning | ready | running | done
    const [basemap, setBasemap] = useState('satellite');
    const [points, setPoints] = useState({ origin: null, destination: null });
    const [phase, setPhase] = useState('idle');
    const [plan, setPlan] = useState(null);                  // {shortest, cells}
    const [values, setValues] = useState({});                // H3 cell -> match (0 poor .. 1 good, null = no score), grows as batches arrive
    const [progress, setProgress] = useState({ done: 0, total: 0 });
    const [understood, setUnderstood] = useState(null);      // the preference event: what Gemini understood
    const [expand, setExpand] = useState(null);              // the latest expand event: the search widened the allowed extra length
    const [result, setResult] = useState(null);
    const [selected, setSelected] = useState(null);
    const [message, setMessage] = useState('');
    const [focus, setFocus] = useState(null);
    const job = useRef({ id: 0, abort: null });

    useEffect(() => {
        setAssistantReady(true);
        if (new URLSearchParams(window.location.search).get('assistant') === 'open') setAssistantOpen(true);
    }, []);

    function closeAssistant() {
        setAssistantOpen(false);
        requestAnimationFrame(() => assistantButton.current?.focus());
    }

    // Forget everything about the current route and cancel work in progress; returns the id of the new job
    function reset() {
        job.current.abort?.abort();
        job.current = { id: job.current.id + 1, abort: new AbortController() };
        setPlan(null); setValues({}); setProgress({ done: 0, total: 0 }); setUnderstood(null); setExpand(null);
        setResult(null); setSelected(null); setMessage('');
        return job.current;
    }

    async function pickPoint(latlng) {
        if (assistantOpen) return;
        if (!inArea(latlng)) { setMessage('Tap inside the dashed outline: that is the area we cover.'); return; }
        const current = reset();
        if (!points.origin || points.destination) {
            setPoints({ origin: latlng, destination: null });
            setPhase('idle');
            return;
        }
        setPoints({ ...points, destination: latlng });
        setPhase('planning');
        try {
            const planned = await DataService.RoutePlan({
                origin: { lat: points.origin.lat, lon: points.origin.lng },
                destination: { lat: latlng.lat, lon: latlng.lng },
            }, current.abort.signal);
            if (current.id !== job.current.id) return;
            setPlan(planned);
            setFocus({ key: `plan-${current.id}`, points: toLatLng([planned.shortest]) });
            setPhase('ready');
        } catch (error) {
            if (current.id !== job.current.id) return;
            setPoints({ origin: points.origin, destination: null });
            setPhase('idle');
            setMessage(error.status === 422 ? 'No route between those two spots. Try another pair.' : 'Could not plan a route. Please try again.');
        }
    }

    // Send the description: cells are coloured as their predictions arrive, then the route options appear
    async function submit(event) {
        event.preventDefault();
        if (!text.trim() || !plan || (phase !== 'ready' && phase !== 'done')) return;
        const kept = plan;
        const current = reset();
        setPlan(kept);
        setPhase('running');
        setProgress({ done: 0, total: kept.cells.length });
        try {
            await DataService.RouteStream({
                origin: { lat: points.origin.lat, lon: points.origin.lng },
                destination: { lat: points.destination.lat, lon: points.destination.lng },
                text: text.trim(),
            }, (e) => {
                if (current.id !== job.current.id) return;
                if (e.type === 'start') setProgress({ done: 0, total: e.cells_total });
                else if (e.type === 'preference') setUnderstood(e);
                else if (e.type === 'expand') {
                    setExpand(e);
                    setProgress(p => ({ ...p, total: p.total + e.cells_added }));
                }
                else if (e.type === 'cells') {
                    setValues(v => ({ ...v, ...Object.fromEntries(e.values) }));
                    setProgress(p => ({ ...p, done: p.done + e.values.length }));
                } else if (e.type === 'result') {
                    setResult(e);
                    setSelected(e.recommended);
                    setFocus({ key: `result-${current.id}`, points: toLatLng(e.routes) });
                    setPhase('done');
                }
            }, current.abort.signal);
        } catch (error) {
            if (current.id !== job.current.id) return;
            setPhase('ready');
            setMessage(error.message || 'Something went wrong. Please try again.');
        }
    }

    const cells = useMemo(() => Object.entries(values).map(([h3_index, value]) => ({ h3_index, value })), [values]);
    const routes = result?.routes ?? (plan ? [plan.shortest] : []);
    const stage = !understood ? 'Understanding your ride…'
        : progress.done < progress.total
            ? `${expand ? `Looking further (up to +${Math.round(expand.max_detour * 100)}% longer)…` : 'Analyzing places…'} ${progress.done}/${progress.total}`
            : 'Finding your best routes…';
    // A wish was understood but no route fits it better than the shortest one: the options are only other streets, say so
    const noBetter = phase === 'done' && result && result.weights.length > 0 && !result.improved;
    const hint = message
        || (phase === 'idle' ? (points.origin ? 'Tap the map to set the destination' : 'Tap the map to set the start')
            : phase === 'planning' ? 'Finding the shortest route…'
                : phase === 'ready' ? `Shortest route ${(plan.shortest.distance_m / 1000).toFixed(1)} km. Now describe your ride`
                    : phase === 'running' ? 'Calculating your best route…'
                        : noBetter ? `No route within +${Math.round(result.max_detour * 100)}% fits your ride better. These are other streets to choose from` : '');
    const canType = phase === 'ready' || phase === 'done';
    const placeholder = phase === 'running' ? stage : phase === 'planning' ? 'Finding the shortest route…'
        : canType ? 'Describe the ride you want' : 'Tap two points on the map first';

    return (
        <main className="stage">
            <div className="phone">
                <div className="screen">
                    <div className="map-content" inert={assistantOpen} aria-hidden={assistantOpen || undefined}>
                    <HexMap cells={cells} points={points} onPick={pickPoint} routes={routes} selected={selected ?? 'shortest'}
                        basemap={basemap} area={AREA} focus={focus} />
                    <div className="island" />
                    {hint && <div className={message ? 'map-hint warn' : 'map-hint'}>{phase === 'running' && !message && <span className="spinner dark" />}{hint}</div>}
                    {phase === 'running' && !message && <div className="map-sub">{stage}</div>}
                    <div className="basemap-toggle" role="group" aria-label="Map style">
                        {[['satellite', 'Satellite'], ['street', 'Map']].map(([id, label]) => (
                            <button key={id} type="button" className={basemap === id ? 'on' : ''} aria-pressed={basemap === id} onClick={() => setBasemap(id)}>{label}</button>
                        ))}
                    </div>
                    {cells.length > 0 && (
                        <div className={result ? 'legend above-cards' : 'legend'}>
                            <i style={{ background: VIRIDIS_GRADIENT }} />
                            <span>Poor match</span><span>Good match</span>
                        </div>
                    )}
                    {result && <RouteCards routes={result.routes} selected={selected} onSelect={setSelected} />}
                    <button ref={assistantButton} type="button" className="assistant-entry" disabled={!assistantReady} onClick={() => setAssistantOpen(true)}
                        aria-haspopup="dialog" aria-expanded={assistantOpen} aria-controls="ride-assistant">Ride Assistant <span aria-hidden="true">↗</span></button>
                    <form className={message ? 'prompt failed' : 'prompt'} onSubmit={submit}>
                        <input
                            value={text}
                            onChange={e => setText(e.target.value)}
                            placeholder={placeholder}
                            aria-label="Ride preference"
                            disabled={!canType}
                        />
                        <button type="submit" aria-label="Find routes" disabled={!canType || !text.trim()}>
                            {phase === 'running' ? <span className="spinner" /> : '↑'}
                        </button>
                    </form>
                    <div className="home-bar" />
                    </div>
                    <RideAssistant open={assistantOpen} onClose={closeAssistant} />
                </div>
            </div>
            <ExplainPanel phase={phase} plan={plan} result={result} selected={selected} understood={understood} progress={progress} />
        </main>
    );
}
