import asyncio
import json
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

ENGINE = web.AppKey('engine', object)
STATIC = web.AppKey('static', Path)
BIND_HOST = web.AppKey('bind_host', str)


def bind_address(value):
    address = ip_address(value)
    if address.is_unspecified or address.is_multicast:
        raise ValueError('Choose a specific local or private-interface IP, not a wildcard or multicast address.')
    return str(address)


@web.middleware
async def request_boundary(request, handler):
    host = urlsplit('//' + request.host).hostname
    if host not in {'127.0.0.1', 'localhost', '::1', request.app[BIND_HOST]}:
        raise web.HTTPForbidden(text='Untrusted host')
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        raise web.HTTPForbidden(text='Untrusted origin')
    try:
        response = await handler(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response
    except (ValueError, TypeError, KeyError) as error:
        return web.json_response({'error': str(error).strip("'")}, status=400)


async def payload(request):
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise ValueError('Request must contain valid JSON')
    if not isinstance(body, dict):
        raise ValueError('Request must be a JSON object')
    return body


async def index(request):
    return web.FileResponse(request.app[STATIC] / 'index.html', headers={'Cache-Control': 'no-store'})


async def script(request):
    return web.FileResponse(request.app[STATIC] / request.match_info['filename'], headers={'Cache-Control': 'no-cache'})


async def state(request):
    return web.json_response(request.app[ENGINE].snapshot(), headers={'Cache-Control': 'no-store'})


async def favorites(request):
    return web.json_response(request.app[ENGINE].favorites(), headers={'Cache-Control': 'no-store'})


async def set_favorite(request):
    return web.json_response(request.app[ENGINE].set_favorite(await payload(request)))


async def track_details(request):
    try:
        result = request.app[ENGINE].track_details(request.query.get('id'))
    except KeyError:
        raise web.HTTPNotFound(text='Track not found')
    return web.json_response(result, headers={'Cache-Control': 'no-store'})


async def generation_next(request):
    return web.json_response(request.app[ENGINE].generation_next(await payload(request)))


async def start(request):
    return web.json_response(request.app[ENGINE].start())


async def stop(request):
    return web.json_response(request.app[ENGINE].stop())


async def preferences(request):
    return web.json_response(request.app[ENGINE].update_preferences(await payload(request)))


async def save_lyrics(request):
    engine = request.app[ENGINE]
    record = engine.save_lyrics(await payload(request))
    return web.json_response({'lyrics': record, 'state': engine.snapshot()})


async def get_lyrics(request):
    record = request.app[ENGINE].lyrics.get(request.query.get('id'))
    if not record:
        raise web.HTTPNotFound(text='Lyrics not found')
    return web.json_response(record)


async def scrape_lyrics(request):
    engine = request.app[ENGINE]
    body = await payload(request)
    source_name = body.get('source', '')
    url = body.get('url')
    if not source_name or not url:
        raise ValueError('Both source and url are required')
    record = await engine.scrape_lyrics(source_name, url)
    return web.json_response({'lyrics': record, 'state': engine.snapshot()})


async def search_lyrics(request):
    engine = request.app[ENGINE]
    body = await payload(request)
    source_name = body.get('source', '')
    query = body.get('q') or body.get('query', '')
    if not source_name or not query:
        raise ValueError('Both source and query are required')
    results = await engine.search_lyrics(source_name, query)
    return web.json_response({'results': results})


async def advance(request):
    return web.json_response(request.app[ENGINE].advance())


async def media(request):
    try:
        path = request.app[ENGINE].media_path(request.query.get('id'), request.query.get('kind'))
    except KeyError:
        raise web.HTTPNotFound(text='Media not found')
    response = web.FileResponse(path, headers={'Cache-Control': 'private, max-age=86400'})
    if request.query.get('download') == '1':
        response.headers['Content-Disposition'] = f'attachment; filename="{path.name}"'
    return response


async def events(request):
    engine = request.app[ENGINE]
    response = web.StreamResponse(headers={
        'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
    })
    await response.prepare(request)
    queue = engine.subscribe()
    try:
        while True:
            try:
                snapshot = await asyncio.wait_for(queue.get(), timeout=15)
                if snapshot is None:
                    break
                message = 'data: ' + json.dumps(snapshot, ensure_ascii=False) + '\n\n'
            except asyncio.TimeoutError:
                message = ': heartbeat\n\n'
            await response.write(message.encode('utf-8'))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        engine.unsubscribe(queue)
    return response


async def redirect(request):
    raise web.HTTPFound('/radio')


async def shutdown(app):
    app[ENGINE].stop()
    app[ENGINE].end_subscriptions()


async def lifecycle(app):
    await app[ENGINE].initialize()
    yield
    await app[ENGINE].close()


def create_app(engine, static_root=None, *, host='127.0.0.1'):
    host = bind_address(host)
    app = web.Application(middlewares=[request_boundary], client_max_size=64 * 1024)
    app[BIND_HOST] = host
    app[ENGINE] = engine
    app[STATIC] = Path(static_root or Path(__file__).resolve().parent.parent / 'static')
    app.cleanup_ctx.append(lifecycle)
    app.on_shutdown.append(shutdown)
    app.router.add_get('/', redirect)
    app.router.add_get('/radio', index)
    app.router.add_get('/radio/{filename:app\\.js|styles\\.css}', script)
    app.router.add_get('/radio/state', state)
    app.router.add_get('/radio/favorites', favorites)
    app.router.add_post('/radio/favorites', set_favorite)
    app.router.add_get('/radio/track', track_details)
    app.router.add_post('/radio/generation/next', generation_next)
    app.router.add_get('/radio/events', events)
    app.router.add_post('/radio/start', start)
    app.router.add_post('/radio/stop', stop)
    app.router.add_post('/radio/preferences', preferences)
    app.router.add_post('/radio/lyrics', save_lyrics)
    app.router.add_get('/radio/lyrics', get_lyrics)
    app.router.add_post('/radio/scrape', scrape_lyrics)
    app.router.add_post('/radio/search', search_lyrics)
    app.router.add_post('/radio/advance', advance)
    app.router.add_get('/radio/media', media)
    return app
