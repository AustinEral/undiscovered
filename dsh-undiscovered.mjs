export const name = 'undiscovered';
export const inject = ['connection', 'webServer'];

const routes = [
  ['', ['GET', 'HEAD']], ['/app.js', ['GET', 'HEAD']], ['/styles.css', ['GET', 'HEAD']],
  ['/state', ['GET']], ['/events', ['GET']], ['/start', ['POST']], ['/stop', ['POST']],
  ['/preferences', ['POST']], ['/lyrics', ['GET', 'POST']], ['/advance', ['POST']],
  ['/media', ['GET', 'HEAD']], ['/favorites', ['GET', 'POST']], ['/track', ['GET']],
  ['/generation/next', ['POST']],
];

export function apiHandler(upstream, suffix, fetchUpstream = fetch) {
  return async (request) => {
    const target = new URL('/radio' + suffix, upstream);
    target.search = new URL(request.url).search;
    const headers = new Headers();
    for (const key of ['content-type', 'range', 'if-range', 'if-none-match', 'if-modified-since']) {
      if (request.headers.has(key)) headers.set(key, request.headers.get(key));
    }
    try {
      const response = await fetchUpstream(target, {
        method: request.method, headers, signal: request.signal,
        body: request.method === 'POST' ? request.body : undefined,
        duplex: request.method === 'POST' ? 'half' : undefined,
      });
      const outgoing = new Headers(response.headers);
      outgoing.delete('set-cookie');
      return new Response(response.body, { status: response.status, headers: outgoing });
    } catch (error) {
      if (request.signal.aborted) throw error;
      return Response.json({ error: 'Undiscovered backend is not running.' }, { status: 502 });
    }
  };
}

export function pageHandler(connection, upstream, fetchPage = fetch) {
  return async (request, response) => {
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405, { Allow: 'GET, HEAD' });
      response.end();
      return;
    }
    const headers = { ...request.headers };
    if (headers['sec-fetch-mode'] === 'navigate' && headers['sec-fetch-dest'] === 'document') {
      headers['sec-fetch-site'] = 'none';
    }
    if (connection.requestRejection({ headers }) === 403) {
      response.writeHead(403);
      response.end('forbidden');
      return;
    }
    if (!connection.authorizeIndex(request, response)) return;
    const abort = new AbortController();
    const cancel = () => abort.abort();
    response.once('close', cancel);
    try {
      const result = await fetchPage(new URL('/radio', upstream), { method: request.method, signal: abort.signal });
      if (result.status !== 200) throw new Error('Player page unavailable');
      const body = request.method === 'HEAD' ? null : Buffer.from(await result.arrayBuffer());
      response.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'same-origin',
        'Content-Security-Policy': "frame-ancestors 'none'",
      });
      response.end(body);
    } catch (error) {
      if (!abort.signal.aborted && !response.headersSent) {
        response.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
        response.end('Undiscovered backend is not running.');
      }
    } finally {
      response.removeListener('close', cancel);
    }
  };
}

export function apply(ctx, config = {}) {
  const upstream = new URL(config.upstream ?? 'http://127.0.0.1:3210');
  if (upstream.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(upstream.hostname)
    || upstream.username || upstream.password) throw new Error('Undiscovered upstream must be a local HTTP server');
  for (const [suffix, methods] of routes) {
    ctx.connection.fetch.register({
      path: '/api/genre-radio' + suffix, methods, fetch: apiHandler(upstream, suffix),
    });
  }
  for (const path of ['/undiscovered', '/genre-radio']) {
    ctx.effect(() => ctx.webServer.register({ kind: 'exact', path, handler: pageHandler(ctx.connection, upstream) }));
  }
}
