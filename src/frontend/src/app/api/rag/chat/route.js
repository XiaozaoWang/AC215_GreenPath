import { apiHeaders } from '@/lib/api-proxy.mjs';
import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

export async function POST(request) {
    let body;
    try {
        body = await request.json();
    } catch {
        return NextResponse.json({ detail: 'Send a valid JSON question.' }, { status: 400 });
    }
    if (typeof body?.question !== 'string' || !body.question.trim() || body.question.trim().length > 2000
        || (body.n_results !== undefined && (!Number.isInteger(body.n_results) || body.n_results < 1 || body.n_results > 10))) {
        return NextResponse.json({ detail: 'Enter a question of 1–2000 characters and a result count of 1–10.' }, { status: 422 });
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 55000);
    try {
        const base = (process.env.API_SERVICE_URL || 'http://localhost:9000').replace(/\/$/, '');
        const response = await fetch(`${base}/rag/chat`, {
            method: 'POST',
            headers: { ...(await apiHeaders(base)), 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: body.question.trim(), n_results: body.n_results ?? 5 }),
            signal: controller.signal,
            cache: 'no-store',
        });
        const result = await response.json();
        return NextResponse.json(result, { status: response.status });
    } catch (error) {
        const timedOut = controller.signal.aborted;
        return NextResponse.json({ detail: timedOut ? 'The assistant timed out. Please try again.' : 'The assistant is unavailable. Please try again later.' },
            { status: timedOut ? 504 : 503 });
    } finally {
        clearTimeout(timer);
    }
}
