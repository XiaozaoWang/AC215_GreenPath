import { proxyApi } from '@/lib/api-proxy.mjs';
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';
export async function GET(request, { params }) {
    const { region_id } = await params;
    if (!/^[A-Za-z0-9_-]+$/.test(region_id)) {
        return Response.json({ detail: 'Invalid region identifier.' }, { status: 400 });
    }
    return proxyApi(`/regions/${encodeURIComponent(region_id)}`);
}
