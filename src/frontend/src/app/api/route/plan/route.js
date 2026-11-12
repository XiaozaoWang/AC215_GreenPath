import { proxyApi } from '@/lib/api-proxy.mjs';
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';
export async function POST(request) { return proxyApi('/route/plan', request); }
