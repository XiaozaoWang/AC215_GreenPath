'use client';
// Next to the phone: explains how the routes are chosen. While a route is being planned it shows the steps; once the result is
// in, it shows why the selected route was picked (a length / match trade-off chart and a per-dimension comparison).
import { ROUTE_COLORS } from './colors';

const NAMES = { shortest: 'Shortest route', efficient: 'Efficient', balanced: 'Balanced', preferred: 'Best match' };
const ROLE = {
    efficient: 'The smallest step up from the shortest route.',
    balanced: 'A middle way between extra length and match.',
    preferred: 'The best match we found.',
};
const pretty = name => name.replace(/_/g, ' ');
const pct = value => `${Math.round(value * 100)}%`;
const signed = value => `${value > 0 ? '+' : '−'}${Math.abs(value).toFixed(1)}`;

// Match against extra length for every route that was found. The line is the best match reachable within each length.
function TradeoffChart({ candidates, selectedKind, maxDetour }) {
    const W = 360, H = 200, m = { l: 46, r: 14, t: 16, b: 38 }, xmax = Math.max(maxDetour, ...candidates.map(c => c.detour));
    const matches = candidates.map(c => c.match);
    const ymin = Math.max(0, Math.min(...matches) - 0.06);
    const ymax = Math.min(1, Math.max(...matches) + 0.06);
    const x = d => m.l + (d / xmax) * (W - m.l - m.r);
    const y = v => H - m.b - ((v - ymin) / (ymax - ymin || 1)) * (H - m.t - m.b);
    const sorted = [...candidates].sort((a, b) => a.detour - b.detour);
    let best = sorted[0].match;
    let path = `M ${x(0)} ${y(best)}`;
    for (const c of sorted) {
        if (c.match > best) { path += ` H ${x(c.detour)} V ${y(c.match)}`; best = c.match; }
    }
    path += ` H ${x(xmax)}`;
    return (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Match against extra length for every route that was found" className="chart">
            {[0.5, 1].map(f => (
                <g key={f}>
                    <line x1={x(f * xmax)} x2={x(f * xmax)} y1={m.t} y2={H - m.b} className="budget" />
                    <text x={x(f * xmax)} y={H - m.b + 14} textAnchor="middle" className="tick">{`+${Math.round(f * xmax * 100)}%`}</text>
                </g>
            ))}
            <text x={x(0)} y={H - m.b + 14} textAnchor="middle" className="tick">0</text>
            <text x={m.l - 6} y={y(ymax) + 4} textAnchor="end" className="tick">{pct(ymax)}</text>
            <text x={m.l - 6} y={y(ymin) + 4} textAnchor="end" className="tick">{pct(ymin)}</text>
            <line x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} className="axis" />
            <line x1={m.l} x2={m.l} y1={m.t} y2={H - m.b} className="axis" />
            <path d={path} className="frontier" />
            {candidates.map((c, i) => c.kind === null && <circle key={i} cx={x(c.detour)} cy={y(c.match)} r="3.5" className="dot" />)}
            {candidates.map((c, i) => c.kind !== null && (
                <g key={`o${i}`}>
                    {c.kind === selectedKind && <circle cx={x(c.detour)} cy={y(c.match)} r="10" className="ring" />}
                    <circle cx={x(c.detour)} cy={y(c.match)} r="6" fill={ROUTE_COLORS[c.kind]} className="option" />
                </g>
            ))}
            <text x={(m.l + W - m.r) / 2} y={H - 4} textAnchor="middle" className="label">Extra length over the shortest route</text>
            <text transform={`translate(12 ${(m.t + H - m.b) / 2}) rotate(-90)`} textAnchor="middle" className="label">Match</text>
        </svg>
    );
}

// For each dimension the user cares about: where the area average, the shortest route and the selected route sit (0 to 1)
function DimensionBars({ result, selected }) {
    const shortest = result.routes.find(r => r.kind === 'shortest');
    const chosen = result.routes.find(r => r.kind === selected);
    const rows = [...result.weights].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight)).slice(0, 6)
        .filter(w => shortest.profile[w.dimension] !== undefined);
    return (
        <div className="dims">
            {rows.map(({ dimension, weight }) => {
                const area = result.area_mean[dimension];
                const a = shortest.profile[dimension], b = chosen.profile[dimension];
                const better = weight > 0 ? b > a : b < a;
                return (
                    <div key={dimension} className="dim">
                        <div className="dim-head">
                            <span>{pretty(dimension)}</span>
                            <em className={weight > 0 ? 'more' : 'less'}>{weight > 0 ? 'you want more' : 'you want less'} · {signed(weight)}</em>
                        </div>
                        <div className="track">
                            {area !== undefined && <i className="area" style={{ left: pct(area) }} title="Area average" />}
                            <b className="short" style={{ left: pct(a) }} title="Shortest route" />
                            {selected !== 'shortest' && <b className="chosen" style={{ left: pct(b), background: ROUTE_COLORS[selected] }} title={NAMES[selected]} />}
                        </div>
                        <div className="dim-foot">
                            shortest {a.toFixed(2)}{selected !== 'shortest' && <> → this route <strong className={better ? 'good' : ''}>{b.toFixed(2)}</strong></>}
                            {area !== undefined && <span> · area average {area.toFixed(2)}</span>}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

function Step({ state, title, children }) {
    return (
        <li className={`step ${state}`}>
            <span className="mark" aria-hidden="true">{state === 'done' ? '✓' : state === 'busy' ? '' : '·'}</span>
            <div><strong>{title}</strong>{children}</div>
        </li>
    );
}

export default function ExplainPanel({ phase, plan, result, selected, understood, progress }) {
    const running = phase === 'running';
    const chosen = result?.routes.find(r => r.kind === selected);
    const shortest = result?.routes.find(r => r.kind === 'shortest');
    return (
        <aside className="explain" aria-label="How the routes are chosen">
            <h2>How your route is chosen</h2>
            <ol className="steps">
                <Step state={plan ? 'done' : phase === 'planning' ? 'busy' : 'todo'} title="1. Shortest route first">
                    {plan && <p>{(plan.shortest.distance_m / 1000).toFixed(1)} km on bike-friendly roads. It is the baseline every other route is compared with, and {plan.cells.length} places around it may be checked.</p>}
                </Step>
                <Step state={understood ? 'done' : running ? 'busy' : 'todo'} title="2. Understand your ride">
                    {understood?.weights?.length > 0 && (
                        <p className="chips">{understood.weights.map(w => (
                            <span key={w.dimension} className={w.weight > 0 ? 'chip more' : 'chip less'}>{pretty(w.dimension)} {signed(w.weight)}</span>
                        ))}</p>
                    )}
                    {understood && understood.weights.length === 0 && <p>No preference found, so only the shortest route is offered.</p>}
                </Step>
                <Step state={result ? 'done' : running && understood ? 'busy' : 'todo'} title="3. Read the satellite images">
                    {(running || result) && progress.total > 0 && (
                        <p>A model scores each place on 15 qualities (trees, water, road width…). <strong>{Math.min(progress.done, progress.total)}</strong> of {progress.total} places done.</p>
                    )}
                </Step>
                <Step state={result ? 'done' : 'todo'} title="4. Find the best trade-off">
                    {result && (
                        <p>Each road costs its length × (1 + s × mismatch). We tried many s and kept the distinct routes: {result.candidates.length} found.
                            {result.max_detour > 0.5 && ` To offer three different options we allowed up to +${Math.round(result.max_detour * 100)}% extra length.`}</p>
                    )}
                </Step>
            </ol>

            {result && chosen && shortest && (
                <section className="why">
                    <h3><i style={{ background: ROUTE_COLORS[selected] }} />{NAMES[selected]}</h3>
                    {selected === 'shortest'
                        ? <p className="lead">This is the shortest way. It matches your ride {pct(chosen.match)}.</p>
                        : chosen.alternative
                            ? <p className="lead">
                                An alternative on different streets ({((chosen.distance_m - shortest.distance_m) / 1000).toFixed(1)} km longer, {pct(chosen.match)} match).
                                {result.improved ? ' Another route is as short and fits at least as well, but you may prefer these streets.'
                                    : ` No route within +${Math.round(result.max_detour * 100)}% fits your ride better than the shortest one, so these are other streets to choose from.`}
                            </p>
                            : <p className="lead">
                                {ROLE[selected]} It adds {((chosen.distance_m - shortest.distance_m) / 1000).toFixed(1)} km ({pct(chosen.detour)}) and raises the match from <strong>{pct(shortest.match)}</strong> to <strong>{pct(chosen.match)}</strong>.
                            </p>}
                    {result.candidates.length > 1 && (<>
                        <h4>Length against match</h4>
                        <TradeoffChart candidates={result.candidates} selectedKind={selected} maxDetour={result.max_detour} />
                        <p className="note">Each dot is a route we found. Coloured dots are the options offered; the line is the best match reachable within a given extra length.</p>
                    </>)}
                    {result.weights.length > 0 && (<>
                        <h4>What the route is made of</h4>
                        <DimensionBars result={result} selected={selected} />
                    </>)}
                </section>
            )}
            {!result && (
                <p className="note">Colours on the map show how well each place matches your ride, from <strong>poor</strong> (purple) to <strong>good</strong> (yellow). They appear as the places are scored.</p>
            )}
        </aside>
    );
}
