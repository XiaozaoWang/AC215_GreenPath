'use client';
// The map inside the phone: a satellite or street basemap, the H3 cells that have been scored so far (coloured by how well
// they match the preference), the start and end points and the route lines.
import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { cellToBoundary } from 'h3-js';
import { matchColor } from './viridis';
import { ROUTE_COLORS } from './colors';

const BASEMAPS = {
    satellite: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', maxZoom: 19 },
    street: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', maxZoom: 19 },
};

// cells: [{h3_index, value}] to draw, value in [0, 1] (0 = poor match, 1 = good match, null = no score); only these are shown.
// points: {origin, destination} as {lat, lng} or null; onPick(latlng): the map was tapped.
// routes: route options (GeoJSON LineStrings, [lon, lat]); selected: the kind drawn thick.
// area: [[south, west], [north, east]] outline of the supported area. focus: {key, points: [[lat, lng], ...]}; the map
// zooms to the points each time the key changes.
export default function HexMap({ cells, points, onPick, routes, selected, basemap, area, focus }) {
    const container = useRef(null);
    const map = useRef(null);
    const base = useRef(null);
    const hexes = useRef({ group: null, layers: new Map() });
    const markers = useRef(null);
    const lines = useRef(null);
    const pick = useRef(onPick);
    pick.current = onPick;

    useEffect(() => {
        const m = L.map(container.current, { zoomControl: false, preferCanvas: true, zoomSnap: 0.25 });
        m.attributionControl.setPrefix(false);
        // Panes keep the order hexes < routes < markers however late each layer is added
        m.createPane('hexes').style.zIndex = 410;
        m.createPane('routes').style.zIndex = 420;
        m.createPane('markers').style.zIndex = 430;
        hexes.current.group = L.layerGroup().addTo(m);
        map.current = m;
        m.on('click', e => pick.current?.(e.latlng));
        // The phone scales with the window: re-measure when it changes, and fit the supported area the first time it has a size
        let fitted = false;
        const observer = new ResizeObserver(() => {
            m.invalidateSize();
            if (!fitted && area && container.current.clientWidth) { m.fitBounds(area); fitted = true; }
        });
        observer.observe(container.current);
        if (area) L.rectangle(area, { color: '#ffffff', weight: 2, dashArray: '8 8', fill: false, interactive: false }).addTo(m);
        return () => { observer.disconnect(); m.remove(); hexes.current = { group: null, layers: new Map() }; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const m = map.current;
        if (!m) return;
        base.current?.remove();
        const { url, maxZoom } = BASEMAPS[basemap] || BASEMAPS.satellite;
        base.current = L.tileLayer(url, { maxZoom, attribution: 'Imagery © Esri' }).addTo(m);
        base.current.bringToBack();
    }, [basemap]);

    // Cells appear as they are scored; existing ones only change colour
    useEffect(() => {
        const { group, layers } = hexes.current;
        if (!group) return;
        const seen = new Set();
        for (const { h3_index: id, value } of cells) {
            seen.add(id);
            const fillColor = matchColor(value);
            const polygon = layers.get(id);
            if (polygon) polygon.setStyle({ fillColor });
            else {
                const created = L.polygon(cellToBoundary(id), {
                    pane: 'hexes', color: '#ffffff', weight: 0.6, opacity: 0.7, fillColor, fillOpacity: 0.75, interactive: false,
                });
                layers.set(id, created);
                group.addLayer(created);
            }
        }
        for (const [id, polygon] of layers) {
            if (!seen.has(id)) { group.removeLayer(polygon); layers.delete(id); }
        }
    }, [cells]);

    useEffect(() => {
        const m = map.current;
        if (!m) return;
        markers.current?.remove();
        const dot = (p, color) => L.circleMarker(p, { pane: 'markers', radius: 8, color: '#ffffff', weight: 3, fillColor: color, fillOpacity: 1, interactive: false });
        markers.current = L.layerGroup([
            points?.origin && dot(points.origin, '#16a34a'),
            points?.destination && dot(points.destination, '#dc2626'),
        ].filter(Boolean)).addTo(m);
    }, [points]);

    // Route lines: white casing under each line so it stands out on any colour; the selected route is drawn last, on top
    useEffect(() => {
        const m = map.current;
        if (!m) return;
        lines.current?.remove();
        lines.current = null;
        if (!routes?.length) return;
        const ordered = [...routes].sort((a, b) => (a.kind === selected) - (b.kind === selected));
        const layers = ordered.flatMap(r => {
            const path = r.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
            const chosen = r.kind === selected;
            const dashed = r.kind === 'shortest' && !chosen ? '8 8' : undefined;
            return [
                L.polyline(path, { pane: 'routes', color: '#ffffff', weight: chosen ? 10 : 6, opacity: 0.9, interactive: false }),
                L.polyline(path, { pane: 'routes', color: ROUTE_COLORS[r.kind], weight: chosen ? 6 : 3, opacity: 1, dashArray: dashed, interactive: false }),
            ];
        });
        lines.current = L.layerGroup(layers).addTo(m);
    }, [routes, selected]);

    // Zoom keeps the routes clear of the prompt at the top and the route cards at the bottom
    useEffect(() => {
        const m = map.current;
        if (!m || !focus?.points?.length) return;
        m.fitBounds(L.latLngBounds(focus.points), { paddingTopLeft: [30, 150], paddingBottomRight: [30, 200] });
    }, [focus?.key]);  // eslint-disable-line react-hooks/exhaustive-deps

    return <div ref={container} className="map" />;
}
