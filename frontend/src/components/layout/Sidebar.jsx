'use client'

// Left nav bar: logo + project name, nav groups (Fabric-style gray group titles + gradient highlight on the current item), backend status light at the bottom.
import { useEffect, useState } from 'react';
import DataService from '@/services/DataService';
import { BASE_API_URL } from '@/services/Common';

// Top-left logo file (in frontend/public/; to switch to the official logo, just overwrite it with the same name)
const LOGO_SRC = '/seas-logo.svg';

// Nav groups: M2 has only one real page; the rest are external links or "coming soon"
const NAV = [
    {
        title: 'Workspace',
        items: [
            { label: 'Scenery heatmap', active: true },
            { label: 'Route planner', soon: true },
            { label: 'Cycling expert', soon: true },
        ],
    },
    {
        title: 'Regions',
        items: [
            { label: 'Active model region', active: true, subtle: true },
        ],
    },
    {
        title: 'Developer',
        items: [
            { label: 'API docs', href: BASE_API_URL + '/docs' },
            { label: 'OpenAPI JSON', href: BASE_API_URL + '/openapi.json' },
        ],
    },
];

export default function Sidebar({ capabilities }) {
    const [apiOnline, setApiOnline] = useState(null);

    // Every 15 seconds ask whether the backend is still alive; the result shows in the status light at the bottom
    useEffect(() => {
        let alive = true;
        const ping = () => DataService.Health()
            .then(() => alive && setApiOnline(true))
            .catch(() => alive && setApiOnline(false));
        ping();
        const id = setInterval(ping, 15000);
        return () => { alive = false; clearInterval(id); };
    }, []);

    return (
        <aside className="hidden h-full w-48 shrink-0 flex-col xl:flex border-r border-slate-200 bg-[#f1f0f7]">
            {/* logo + project name */}
            <div className="flex items-center gap-3 px-4 pb-4 pt-5">
                <img src={LOGO_SRC} alt="Harvard SEAS" className="h-9 w-9 rounded-lg object-contain" />
                <div className="leading-tight">
                    <div className="text-base font-semibold text-slate-900">SmartPath</div>
                    <div className="text-xs text-slate-500">AC215 · Harvard SEAS</div>
                </div>
            </div>

            {/* Nav groups */}
            <nav className="flex flex-1 flex-col gap-4 overflow-y-auto px-3">
                {NAV.map(group => group.title === 'Regions' && capabilities ? { ...group, items: capabilities.regions.map(r => ({ label: r.label, active: true, subtle: true })) } : group).map((group) => (
                    <div key={group.title}>
                        <div className="mb-1 rounded-md bg-slate-200/[0.6] px-2.5 py-1 text-xs font-semibold text-slate-700">{group.title}</div>
                        <div className="flex flex-col">
                            {group.items.map((item) => <NavItem key={item.label} item={item} />)}
                        </div>
                    </div>
                ))}
            </nav>

            {/* Bottom: backend status light */}
            <div className="flex items-center gap-2 border-t border-slate-200 px-4 py-3 text-xs text-slate-600">
                <span className={`h-2 w-2 rounded-full ${apiOnline === null ? 'bg-slate-300' : apiOnline ? 'bg-emerald-500' : 'bg-red-500'}`} />
                {apiOnline === null ? 'Checking API…' : apiOnline ? 'API online' : 'API offline'}
                <span className="ml-auto text-slate-400">M2</span>
            </div>
        </aside>
    );
}

// One nav item: current page = gradient highlight; external link = opens in a new tab; coming soon = grayed out
function NavItem({ item }) {
    const base = 'mt-0.5 flex items-center rounded-md px-2.5 py-1.5 text-sm';
    if (item.active && !item.subtle) {
        return <div className={`${base} bg-gradient-to-r from-violet-500 to-pink-400 font-medium text-white shadow-sm`}>{item.label}</div>;
    }
    if (item.active) {
        return <div className={`${base} bg-white font-medium text-slate-800 shadow-sm`}>{item.label}</div>;
    }
    if (item.href) {
        return <a href={item.href} target="_blank" rel="noreferrer" className={`${base} text-slate-700 hover:bg-white`}>{item.label}<span className="ml-auto text-slate-400">↗</span></a>;
    }
    return <div className={`${base} cursor-default text-slate-400`}>{item.label}<span className="ml-auto rounded bg-slate-200 px-1.5 text-[10px] text-slate-500">soon</span></div>;
}
