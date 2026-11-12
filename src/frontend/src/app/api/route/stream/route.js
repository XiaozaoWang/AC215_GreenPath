import { proxyApiStream } from '@/lib/api-proxy.mjs';
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';
export async function POST(request) { return proxyApiStream('/route/stream', request); }
