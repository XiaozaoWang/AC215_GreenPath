'use client';
// 手机里的地图：底下是卫星图，上面是 H3 六边形，每个格子按总分上色。
import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { cellToBoundary } from 'h3-js';
import { colorFor, rgb } from './colors';

// Esri 免费卫星底图（右下角显示版权）
const SATELLITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

export default function HexMap({ cells, maxAbs }) {
    const container = useRef(null);
    const map = useRef(null);
    const hexes = useRef(null);

    // 只建一次地图：不要缩放按钮（滚轮 / 双指缩放，拖动平移）
    useEffect(() => {
        const m = L.map(container.current, { zoomControl: false, preferCanvas: true, zoomSnap: 0.25 });
        m.attributionControl.setPrefix(false);
        L.tileLayer(SATELLITE, { maxZoom: 19, attribution: 'Imagery © Esri' }).addTo(m);
        map.current = m;
        // 手机的尺寸是随窗口算出来的：尺寸一变就让地图重新量一次，并重新对准这片区域
        const observer = new ResizeObserver(() => {
            m.invalidateSize();
            if (hexes.current) m.fitBounds(L.featureGroup(hexes.current.getLayers()).getBounds());
        });
        observer.observe(container.current);
        return () => { observer.disconnect(); m.remove(); };
    }, []);

    // 每次有新的格子分数：重画六边形；第一次画时把视野对准这片区域
    useEffect(() => {
        const m = map.current;
        if (!m || !cells.length) return;
        const first = !hexes.current;
        hexes.current?.remove();
        hexes.current = L.layerGroup(cells.map(c => L.polygon(cellToBoundary(c.h3_index), {
            color: '#ffffff', weight: 0.6, opacity: 0.7, fillColor: rgb(colorFor(c.value, maxAbs)), fillOpacity: 0.75, interactive: false,
        }))).addTo(m);
        if (first) {
            m.invalidateSize();
            m.fitBounds(L.featureGroup(hexes.current.getLayers()).getBounds());
        }
    }, [cells, maxAbs]);

    return <div ref={container} className="map" />;
}
