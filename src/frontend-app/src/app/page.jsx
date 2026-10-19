'use client';
// The whole page: one iPhone 16 mockup on a white background. The phone screen holds only the map and the prompt box.
import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';
import DataService from '@/services/DataService';

// Leaflet needs the browser window, so the map renders on the client only
const HexMap = dynamic(() => import('@/components/HexMap'), { ssr: false });

// Before the first sentence, predicted cells are drawn neutral (score 0) so they stand out from the gray cells
const neutral = (features) => features.map(f => ({ ...f, properties: { ...f.properties, value: 0 } }));

export default function Home() {
    const [features, setFeatures] = useState([]);
    const [maxAbs, setMaxAbs] = useState(1);
    const [text, setText] = useState('');
    const [loading, setLoading] = useState(false);
    const [failed, setFailed] = useState(false);

    // Load the region once (saved predictions; no LLM call)
    useEffect(() => {
        DataService.Preview().then(r => setFeatures(neutral(r.features))).catch(() => setFailed(true));
    }, []);

    // Send the sentence, then recolor the map with the new scores
    async function submit(event) {
        event.preventDefault();
        if (!text.trim() || loading) return;
        setLoading(true);
        setFailed(false);
        try {
            const result = await DataService.Score(text.trim());
            setFeatures(result.features);
            setMaxAbs(Math.max(Math.abs(result.stats.t_min ?? 0), Math.abs(result.stats.t_max ?? 0)) || 1);
        } catch {
            setFailed(true);
        } finally {
            setLoading(false);
        }
    }

    return (
        <main className="stage">
            <div className="phone">
                <div className="screen">
                    <HexMap features={features} maxAbs={maxAbs} />
                    <div className="island" />
                    <form className={failed ? 'prompt failed' : 'prompt'} onSubmit={submit}>
                        <input
                            id="ride-prompt"
                            value={text}
                            onChange={e => setText(e.target.value)}
                            placeholder={failed ? 'Something went wrong. Try again' : 'Describe the ride you want'}
                            aria-label="Ride preference"
                            disabled={loading}
                        />
                        <button type="submit" aria-label="Show matching streets" disabled={loading || !text.trim()}>
                            {loading ? <span className="spinner" /> : '↑'}
                        </button>
                    </form>
                    <div className="home-bar" />
                </div>
            </div>
        </main>
    );
}
