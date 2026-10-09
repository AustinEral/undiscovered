import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import { pageHandler, apiHandler, apply } from '../dsh-undiscovered.mjs';

function response() {
  const result = new EventEmitter();
  result.writeHead = (status, headers = {}) => { result.status = status; result.headers = headers; result.headersSent = true; };
  result.end = body => { result.body = body; };
  return result;
}

function connection(authenticated = true) {
  return {
    requestRejection({ headers }) {
      if (headers.host !== 'radio.example.com' || headers['sec-fetch-site'] === 'cross-site') return 403;
      if (headers.origin && headers.origin !== 'https://radio.example.com') return 403;
      return authenticated ? undefined : 401;
    },
    authorizeIndex(request, res) {
      if (!authenticated) { res.writeHead(401); res.end('unauthorized'); }
      return authenticated;
    },
  };
}

const navigation = { host:'radio.example.com', 'sec-fetch-site':'cross-site', 'sec-fetch-mode':'navigate', 'sec-fetch-dest':'document', 'sec-fetch-user':'?1' };

test('Undiscovered keeps legacy access and registers library/recovery APIs', () => {
  const api = [], pages = [];
  const context = {
    connection: {fetch:{register: route => api.push(route)}},
    webServer: {register: route => { pages.push(route); return () => {}; }},
    effect: effect => effect(),
  };
  apply(context);
  assert.deepEqual(pages.map(route => route.path), ['/undiscovered', '/genre-radio']);
  assert.deepEqual(api.find(route => route.path === '/api/genre-radio/favorites').methods, ['GET', 'POST']);
  assert.deepEqual(api.find(route => route.path === '/api/genre-radio/track').methods, ['GET']);
  assert.deepEqual(api.find(route => route.path === '/api/genre-radio/generation/next').methods, ['POST']);
  for (const upstream of ['https://example.com', 'http://example.com', 'http://user:pass@localhost:3210']) {
    assert.throws(() => apply(context, {upstream}), /local HTTP/);
  }
});

test('API bridge preserves local path/query/ranges and never forwards cookies', async () => {
  let target, options;
  const handler = apiHandler('http://127.0.0.1:3210', '/track', async (url, init) => {
    target = url;
    options = init;
    return new Response('result', {headers:{'set-cookie':'not-for-client','content-type':'application/json'}});
  });
  const result = await handler(new Request('https://radio.example.com/api/genre-radio/track?id=fixture', {headers:{range:'bytes=0-9',cookie:'private=secret'}}));
  assert.equal(target.href, 'http://127.0.0.1:3210/radio/track?id=fixture');
  assert.equal(options.headers.get('range'), 'bytes=0-9');
  assert.equal(options.headers.get('cookie'), null);
  assert.equal(result.headers.get('set-cookie'), null);
  assert.equal(await result.text(), 'result');
});

test('Favorite mutations stream their body only to the fixed upstream route', async () => {
  let body;
  const handler = apiHandler('http://127.0.0.1:3210', '/favorites', async (url, init) => {
    assert.equal(url.pathname, '/radio/favorites');
    assert.equal(init.method, 'POST');
    assert.equal(init.duplex, 'half');
    body = await new Response(init.body).text();
    return Response.json({state:{}});
  });
  const result = await handler(new Request('https://radio.example.com/api/genre-radio/favorites', {method:'POST',body:'{"id":"fixture","starred":true}',headers:{'content-type':'application/json'}}));
  assert.equal(result.status, 200);
  assert.deepEqual(JSON.parse(body), {id:'fixture',starred:true});
});

test('safe top-level link navigation serves the page without mutating request headers', async () => {
  let calls = 0;
  const handler = pageHandler(connection(), 'http://127.0.0.1:3210', async () => { calls++; return new Response('<html>player</html>'); });
  const request = { method:'GET', headers:{...navigation} };
  const res = response();
  await handler(request, res);
  assert.equal(res.status, 200);
  assert.equal(calls, 1);
  assert.equal(request.headers['sec-fetch-site'], 'cross-site');
  assert.equal(res.headers['Content-Security-Policy'], "frame-ancestors 'none'");
});

test('foreign hosts, origins, cross-site fetches and frames remain forbidden', async () => {
  const handler = pageHandler(connection(), 'http://127.0.0.1:3210', async () => { throw new Error('Must not fetch'); });
  for (const headers of [
    {...navigation,host:'attacker.example.com'}, {...navigation,origin:'https://attacker.example.com'},
    {...navigation,'sec-fetch-mode':'cors','sec-fetch-dest':'empty'}, {...navigation,'sec-fetch-dest':'iframe'},
  ]) {
    const res = response();
    await handler({method:'GET',headers},res);
    assert.equal(res.status,403);
  }
});

test('page login remains authoritative and non-read methods are refused', async () => {
  const handler = pageHandler(connection(false), 'http://127.0.0.1:3210', async () => { throw new Error('Must not fetch'); });
  const unauthenticated = response();
  await handler({method:'GET',headers:navigation},unauthenticated);
  assert.equal(unauthenticated.status,401);
  const mutation = response();
  await handler({method:'POST',headers:navigation},mutation);
  assert.equal(mutation.status,405);
});

test('HEAD returns no body and unavailable upstream has a bounded error', async () => {
  const handler = pageHandler(connection(), 'http://127.0.0.1:3210', async () => new Response(null));
  const res = response();
  await handler({method:'HEAD',headers:{host:'radio.example.com'}},res);
  assert.equal(res.status,200);
  assert.equal(res.body,null);
  const failed = response();
  await pageHandler(connection(), 'http://127.0.0.1:3210', async () => { throw new Error('offline'); })({method:'GET',headers:{host:'radio.example.com'}},failed);
  assert.equal(failed.status,502);
});
