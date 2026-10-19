'use client';
// The map inside the phone: satellite imagery underneath, H3 hexagons on top.
// Colored hexagon = a cell the model predicted; gray hexagon = no prediction there.
import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { cellToBoundary, getResolution, polygonToCells } from 'h3-js';
import { NO_DATA, colorFor, rgb } from './colors';

// Esri World Imagery: free satellite basemap tiles (credit shown in the corner)
const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

export default function HexMap({ features, maxAbs }) {
    const container = useRef(null);
    const map = useRef(null);
    const grid = useRef(null);
    const hexes = useRef(null);

    // Create the map once: no zoom buttons (scroll / pinch to zoom, drag to pan)
    useEffect(() => {
        const m = L.map(container.current, { zoomControl: false, preferCanvas: true, zoomSnap: 0.25, maxBoundsViscosity: 1 });
        m.attributionControl.setPrefix(false);
        L.tileLayer(SATELLITE, { maxZoom: 19, attribution: 'Imagery © Esri' }).addTo(m);
        map.current = m;
        return () => m.remove();
    }, []);

    // First data: fit the region, keep the view inside it (no blank edges), and fill it with gray hexagons
    useEffect(() => {
        const m = map.current;
        if (!m || !features.length || grid.current) return;
        const region = L.geoJSON(features).getBounds().pad(0.15);
        m.invalidateSize();
        const minZoom = m.getBoundsZoom(region, true);
        m.setMinZoom(minZoom);
        m.setMaxBounds(region);
        m.setView(region.getCenter(), minZoom);

        // Every H3 cell of the same resolution that covers the region, minus the predicted ones
        const outer = region.pad(0.05);
        const box = [[outer.getSouth(), outer.getWest()], [outer.getSouth(), outer.getEast()],
                     [outer.getNorth(), outer.getEast()], [outer.getNorth(), outer.getWest()]];
        const predicted = new Set(features.map(f => f.properties.h3_index));
        const style = { color: '#ffffff', weight: 0.6, opacity: 0.45, fillColor: rgb(NO_DATA), fillOpacity: 0.55, interactive: false };
        const cells = polygonToCells(box, getResolution(features[0].properties.h3_index)).filter(c => !predicted.has(c));
        grid.current = L.layerGroup(cells.map(c => L.polygon(cellToBoundary(c), style))).addTo(m);
    }, [features]);

    // Every new result: redraw the predicted hexagons in their score colors, on top of the gray grid
    useEffect(() => {
        const m = map.current;
        if (!m) return;
        hexes.current?.remove();
        hexes.current = L.geoJSON(features, {
            interactive: false,
            style: f => ({ color: '#ffffff', weight: 0.8, opacity: 0.8, fillColor: rgb(colorFor(f.properties.value, maxAbs)), fillOpacity: 0.78 }),
        }).addTo(m);
    }, [features, maxAbs]);

    return <div ref={container} className="map" />;
}
