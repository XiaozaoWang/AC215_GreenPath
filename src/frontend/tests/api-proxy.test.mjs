import assert from 'node:assert/strict';
import { afterEach, mock, test } from 'node:test';
import { GoogleAuth } from 'google-auth-library';
import { apiHeaders, proxyApi, proxyApiStream } from '../src/lib/api-proxy.mjs';
import { readNdjson } from '../src/lib/ndjson.mjs';

afterEach(() => { mock.restoreAll(); delete process.env.API_SERVICE_URL; });

test('local HTTP skips tokens; HTTPS uses the API base URL as audience', async () => {
    const token = mock.method(GoogleAuth.prototype, 'getIdTokenClient', async (audience) => {
        assert.equal(audience, 'https://api.run.app');
        return { getRequestHeaders: async () => new Headers({ authorization: 'Bearer server-token' }) };
    });
    assert.deepEqual(await apiHeaders('http://localhost:9000'), {});
    assert.equal(token.mock.callCount(), 0);
    process.env.API_SERVICE_URL = 'https://api.run.app/';
    mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, 'https://api.run.app/score');
        assert.equal(options.headers.authorization, 'Bearer server-token');
        assert.deepEqual(JSON.parse(options.body), { text: 'shade' });
        return Response.json({ cells: [{ value: null, status: 'mostly_black' }] });
    });
    const request = new Request('http://frontend/api/score', { method: 'POST', body: JSON.stringify({ text: 'shade' }) });
    const response = await proxyApi('/score', request);
    assert.equal((await response.json()).cells[0].value, null);
    assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('forwards backend errors and rejects invalid browser JSON', async () => {
    const fetcher = mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'forbidden' }, { status: 403 }));
    assert.equal((await proxyApi('/regions/boston')).status, 403);
    const request = new Request('http://frontend/api/score', { method: 'POST', body: 'bad json' });
    assert.equal((await proxyApi('/score', request)).status, 400);
    assert.equal(fetcher.mock.callCount(), 1);
});

test('connection failures and timeouts return safe errors', async () => {
    mock.method(globalThis, 'fetch', async () => { throw new Error('private detail'); });
    let response = await proxyApi('/score');
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes('private detail'));
    process.env.API_SERVICE_URL = 'https://api.run.app';
    mock.method(GoogleAuth.prototype, 'getIdTokenClient', async () => { throw new Error('credential detail'); });
    response = await proxyApi('/score');
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes('credential detail'));
    delete process.env.API_SERVICE_URL;
    mock.method(AbortSignal, 'timeout', () => AbortSignal.abort());
    response = await proxyApi('/score');
    assert.equal(response.status, 504);
});

test('streams NDJSON events through without buffering and forwards early errors', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
        start(controller) {
            controller.enqueue(encoder.encode('{"type":"start"}\n{"type":"ce'));
            controller.enqueue(encoder.encode('lls"}\n'));
            controller.close();
        },
    });
    const fetcher = mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, 'http://localhost:9000/route/stream');
        assert.deepEqual(JSON.parse(options.body), { text: 'shade' });
        return new Response(stream, { headers: { 'content-type': 'application/x-ndjson' } });
    });
    const request = new Request('http://frontend/api/route/stream', { method: 'POST', body: JSON.stringify({ text: 'shade' }) });
    const response = await proxyApiStream('/route/stream', request);
    assert.equal(response.headers.get('content-type'), 'application/x-ndjson');
    const events = [];
    for await (const event of readNdjson(response.body)) events.push(event.type);
    assert.deepEqual(events, ['start', 'cells']);          // a line split across two chunks is rejoined

    fetcher.mock.restore();
    mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'outside the area' }, { status: 422 }));
    const failed = await proxyApiStream('/route/stream', new Request('http://frontend/x', { method: 'POST', body: '{}' }));
    assert.equal(failed.status, 422);
    assert.equal((await failed.json()).detail, 'outside the area');
    assert.equal((await proxyApiStream('/route/stream', new Request('http://frontend/x', { method: 'POST', body: 'bad' }))).status, 400);
});
