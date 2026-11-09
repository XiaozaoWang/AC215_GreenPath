'use client';
// 整个页面：白底上一部 iPhone 16。屏幕里只有地图和输入框。
import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';
import DataService from '@/services/DataService';

// Leaflet 要用浏览器的 window，所以地图只在浏览器里渲染
const HexMap = dynamic(() => import('@/components/HexMap'), { ssr: false });

export default function Home() {
    const [cells, setCells] = useState([]);
    const [maxAbs, setMaxAbs] = useState(1);
    const [text, setText] = useState('');
    const [loading, setLoading] = useState(false);
    const [failed, setFailed] = useState(false);

    // 打开页面：先画出整片区域，格子都是 0 分（近白色）
    useEffect(() => {
        DataService.Region().then(r => setCells(r.h3_index.map(h => ({ h3_index: h, value: 0 })))).catch(() => setFailed(true));
    }, []);

    // 发送这句话，拿回每个格子的总分后重新上色
    async function submit(event) {
        event.preventDefault();
        if (!text.trim() || loading) return;
        setLoading(true);
        setFailed(false);
        try {
            const result = await DataService.Score(text.trim());
            setCells(result.cells);
            // 颜色两端的值：取正负两边绝对值更大的那个，让 0（区域平均）永远是近白色
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
                    <HexMap cells={cells} maxAbs={maxAbs} />
                    <div className="island" />
                    <form className={failed ? 'prompt failed' : 'prompt'} onSubmit={submit}>
                        <input
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
