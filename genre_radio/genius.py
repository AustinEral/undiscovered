import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp

BASE_URL = 'https://genius.com'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_LYRICS_CHARS = 14000
_HOSTS = {'genius.com', 'www.genius.com'}
_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
_IGNORED = {'script', 'style', 'template', 'noscript', 'button', 'nav', 'footer'}
_BLOCKS = {'div', 'p', 'section', 'li'}


class GeniusError(ValueError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status
        self.temporary = status == 429 or (status is not None and status >= 500)


def validate_url(url):
    if not isinstance(url, str) or not url or any(ord(char) < 33 for char in url):
        raise GeniusError('Provide a Genius HTTPS URL')
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme == 'https' and parsed.hostname in _HOSTS and parsed.port in (None, 443)
    except ValueError as error:
        raise GeniusError('Provide a Genius HTTPS URL') from error
    if not valid or parsed.username is not None or parsed.password is not None or parsed.path.startswith('//'):
        raise GeniusError('Provide a Genius HTTPS URL')
    return urlunsplit(('https', parsed.hostname, parsed.path, parsed.query, ''))


def song_url(url):
    if isinstance(url, str) and url.startswith(('genius.com/', 'www.genius.com/')):
        url = 'https://' + url
    normalized = validate_url(url)
    parsed = urlsplit(normalized)
    path = parsed.path.rstrip('/')
    if not re.fullmatch(r'/[^/]+-lyrics', path, re.IGNORECASE):
        raise GeniusError('Provide a Genius song URL ending in -lyrics')
    return urlunsplit(('https', parsed.hostname, path, '', ''))


async def fetch_text(session, url, *, song_page=False):
    current = song_url(url) if song_page else validate_url(url)
    for redirects in range(4):
        async with session.get(current, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=20), headers={'User-Agent': 'Undiscovered/1.0'}) as response:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get('Location')
                if not location or redirects == 3:
                    raise GeniusError('Genius returned an invalid or excessive redirect')
                target = urljoin(current, location)
                current = song_url(target) if song_page else validate_url(target)
                continue
            if response.status != 200:
                if response.status == 429:
                    message = 'Genius rate-limited the request (HTTP 429). Try again later.'
                elif response.status in {401, 403}:
                    message = f'Genius refused access (HTTP {response.status}); no access bypass was attempted.'
                else:
                    message = f'Genius request failed: HTTP {response.status}'
                raise GeniusError(message, status=response.status)
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise GeniusError('Genius response exceeded the size limit')
            try:
                return data.decode(response.charset or 'utf-8')
            except (UnicodeError, LookupError) as error:
                raise GeniusError('Genius returned an invalid text encoding') from error
    raise GeniusError('Genius returned too many redirects')


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = None
        self.groups = []
        self.meta = {}
        self.title = []
        self.jsonld = []
        self.script_parts = None

    def _break(self):
        if self.parts and not self.parts[-1].endswith('\n'):
            self.parts.append('\n')

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        inherited = bool(self.stack and self.stack[-1][1])
        excluded = inherited or tag in _IGNORED or attributes.get('data-exclude-from-selection') == 'true' or attributes.get('aria-hidden') == 'true' or 'hidden' in attributes or 'lyricsheader' in (attributes.get('class') or '').lower()
        if tag == 'meta':
            key = (attributes.get('property') or attributes.get('name') or '').lower()
            if key and attributes.get('content'):
                self.meta[key] = attributes['content']
        if tag == 'script' and (attributes.get('type') or '').lower() == 'application/ld+json':
            self.script_parts = []
        root = self.parts is None and not excluded and attributes.get('data-lyrics-container') == 'true'
        if root:
            self.parts = []
        elif self.parts is not None and not excluded and tag in _BLOCKS:
            self._break()
        if self.parts is not None and not excluded and tag == 'br':
            self.parts.append('\n')
        if tag not in _VOID:
            self.stack.append((tag, excluded, root))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        frames = self.stack[index:]
        del self.stack[index:]
        if tag == 'script' and self.script_parts is not None:
            self.jsonld.append(''.join(self.script_parts))
            self.script_parts = None
        if any(frame[2] for frame in frames):
            text = ''.join(self.parts or []).strip()
            if text:
                self.groups.append(text)
            self.parts = None
        elif self.parts is not None and not frames[0][1] and tag in _BLOCKS:
            self._break()

    def handle_data(self, data):
        if self.script_parts is not None:
            self.script_parts.append(data)
        if self.stack and self.stack[-1][0] == 'title':
            self.title.append(data)
        if self.parts is not None and self.stack and not self.stack[-1][1]:
            self.parts.append(data)


def _parse(html):
    if not isinstance(html, str) or len(html) > MAX_RESPONSE_BYTES:
        raise GeniusError('Genius page is invalid or exceeds the size limit')
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    if parser.parts is not None:
        raise GeniusError('Genius returned an incomplete lyrics container')
    return parser


def extract_lyrics(html):
    groups = _parse(html).groups
    if not groups:
        raise GeniusError('Genius returned no lyric text for the selected song')
    groups[0] = re.sub(r'^\s*(?:\d+\s*)?Contributors?Translations.*? Lyrics(?:Read More)?\s*', '', groups[0], count=1, flags=re.DOTALL)
    text = '\n\n'.join(groups)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if len(text) < 100 or text.count('\n') < 4:
        raise GeniusError('Genius returned incomplete lyric text for the selected song')
    if len(text) > MAX_LYRICS_CHARS:
        raise GeniusError('Genius lyrics exceed the 14000-character input limit; text was not truncated')
    return text


def original_genre(html):
    for pattern in (r'"primaryTag"\s*:\s*"([^"\\]+)"', r'\\"primaryTag\\"\s*:\s*\\"([^"\\]+)\\"'):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return ''


def _recording(parser):
    for script in parser.jsonld:
        try:
            pending = [json.loads(script)]
        except (ValueError, RecursionError):
            continue
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(reversed(item))
            elif isinstance(item, dict):
                if item.get('@type') == 'MusicRecording' or isinstance(item.get('@type'), list) and 'MusicRecording' in item['@type']:
                    yield item
                graph = item.get('@graph')
                if isinstance(graph, (dict, list)):
                    pending.append(graph)


def page_metadata(html):
    parser = _parse(html)
    for item in _recording(parser):
        artists = item.get('byArtist', [])
        artists = artists if isinstance(artists, list) else [artists]
        names = [artist.get('name', '') if isinstance(artist, dict) else artist for artist in artists]
        title = item.get('name')
        if isinstance(title, str) and title.strip() and names and all(isinstance(name, str) and name.strip() for name in names):
            genre = item.get('genre')
            return {'title': title.strip(), 'artist': ', '.join(name.strip() for name in names), 'genre': genre if isinstance(genre, str) else original_genre(html) or None}
    heading = parser.meta.get('og:title') or parser.meta.get('twitter:title') or ''.join(parser.title)
    heading = re.sub(r'\s*\|\s*Genius(?:\s+Lyrics)?\s*$', '', heading, flags=re.IGNORECASE)
    heading = re.sub(r'\s+Lyrics\s*$', '', heading, flags=re.IGNORECASE)
    parts = re.split(r'\s+[–—-]\s+', heading, maxsplit=1)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise GeniusError('Genius page is missing song title or artist metadata')
    return {'artist': parts[0].strip(), 'title': parts[1].strip(), 'genre': original_genre(html) or None}


def search_results(payload):
    try:
        sections = json.loads(payload)['response']['sections']
        if not isinstance(sections, list):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise GeniusError('Genius returned malformed search data') from error
    results = []
    seen = set()
    for section in sections:
        if not isinstance(section, dict):
            continue
        hits = section.get('hits', [])
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, dict) or hit.get('index') != 'song':
                continue
            item = hit.get('result')
            if not isinstance(item, dict) or item.get('instrumental') or item.get('lyrics_state', 'complete') != 'complete':
                continue
            title = item.get('title')
            artist = item.get('artist_names')
            if not artist and isinstance(item.get('primary_artist'), dict):
                artist = item['primary_artist'].get('name')
            if not isinstance(title, str) or not title.strip() or not isinstance(artist, str) or not artist.strip():
                continue
            try:
                url = song_url(item.get('url'))
            except GeniusError:
                continue
            if url in seen:
                continue
            seen.add(url)
            stats = item.get('stats') or {}
            views = stats.get('pageviews') if isinstance(stats, dict) else None
            results.append({'title': title.strip(), 'artist': artist.strip(), 'source_url': url, 'views': views if type(views) is int and views >= 0 else None})
    return results[:12]
