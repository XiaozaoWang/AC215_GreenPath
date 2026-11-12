import { GoogleAuth } from 'google-auth-library';

const auth = new GoogleAuth();

// Cloud Run uses the frontend service account. Local HTTP development needs no token.
export async function apiHeaders(base) {
    if (new URL(base).protocol !== 'https:') return {};
    const client = await auth.getIdTokenClient(base);
    return Object.fromEntries((await client.getRequestHeaders()).entries());
}

// Next.js route handlers supply fixed API paths; browser input never chooses the upstream host.
export async function proxyApi(path, request) {
    let body;
    if (request) {
        try { body = await request.json(); }
        catch { return Response.json({ detail: 'Send valid JSON.' }, { status: 400 }); }
    }
    const base = (process.env.API_SERVICE_URL || 'http://localhost:9000').replace(/\/+$/, '');
    const signal = AbortSignal.timeout(900000); // Full-region inference can take several minutes.
    try {
        const upstream = await fetch(`${base}${path}`, {
            method: request ? 'POST' : 'GET',
            headers: { ...(await apiHeaders(base)), 'Content-Type': 'application/json' },
            body: request ? JSON.stringify(body) : undefined,
            cache: 'no-store', signal,
        });
        return Response.json(await upstream.json(), {
            status: upstream.status, headers: { 'Cache-Control': 'no-store' },
        });
    } catch {
        return Response.json({ detail: signal.aborted ? 'The API request timed out.' : 'The API service is unavailable.' },
            { status: signal.aborted ? 504 : 502 });
    }
}
