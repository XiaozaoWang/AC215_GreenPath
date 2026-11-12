'use client';
// One card per route option returned by POST /route; tapping a card shows that route on the map.
import { ROUTE_COLORS } from './colors';

const LABELS = { shortest: 'Shortest', efficient: 'Efficient', balanced: 'Balanced', preferred: 'Best match' };

export default function RouteCards({ routes, selected, onSelect }) {
    return (
        <div className="route-cards" role="radiogroup" aria-label="Route options">
            {routes.map(r => (
                <button key={r.kind} type="button" role="radio" aria-checked={r.kind === selected}
                    className={r.kind === selected ? 'route-card selected' : 'route-card'} onClick={() => onSelect(r.kind)}>
                    <span className="route-card-title">
                        <i style={{ background: ROUTE_COLORS[r.kind] }} />{LABELS[r.kind]}
                    </span>
                    <span><strong>{(r.distance_m / 1000).toFixed(1)} km</strong>{r.kind !== 'shortest' && ` +${Math.round(r.detour * 100)}%`}</span>
                    <span>{Math.round(r.match * 100)}% match{r.alternative && <em className="alt"> · other streets</em>}</span>
                </button>
            ))}
        </div>
    );
}
