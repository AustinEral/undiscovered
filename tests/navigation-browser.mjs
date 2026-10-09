import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const privateOrigin = new URL(process.env.RADIO_PRIVATE_ORIGIN).origin;
const sourceURL = process.env.RADIO_SOURCE_URL || 'http://127.0.0.1:3210/radio';
const pagePath = process.env.RADIO_PAGE_PATH || '/undiscovered';
const output = process.env.UI_SCREENSHOT_DIR || await mkdtemp(join(tmpdir(), 'genre-radio-navigation-'));
const profile = await mkdtemp(join(tmpdir(), 'genre-radio-navigation-chrome-'));
const browser = spawn(process.env.CHROME_BIN || '/usr/bin/google-chrome', ['--headless=new', '--mute-audio', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let stderr = '';
let socket;
browser.stderr.on('data', (data) => { stderr += data; });
const until = async (check, message, timeout = 15000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(50);
  }
  throw new Error(message);
};

try {
  await until(() => /DevTools listening on (ws:\/\/\S+)/.test(stderr), 'Chrome did not start');
  socket = new WebSocket(stderr.match(/DevTools listening on (ws:\/\/\S+)/)[1]);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  const pending = new Map();
  const requests = new Map();
  const responses = [];
  const mutations = [];
  const blockedMutations = [];
  let nextId = 0;
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const operation = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) operation.reject(new Error(JSON.stringify(message.error)));
      else operation.resolve(message.result);
      return;
    }
    const params = message.params;
    if (message.method === 'Fetch.requestPaused') {
      const readOnly = ['GET', 'HEAD', 'OPTIONS'].includes(params.request.method);
      if (!readOnly) blockedMutations.push(params.request);
      void send(readOnly ? 'Fetch.continueRequest' : 'Fetch.failRequest', { requestId: params.requestId, ...(!readOnly ? { errorReason: 'Aborted' } : {}) }, message.sessionId);
    }
    if (message.method === 'Network.requestWillBeSent') {
      requests.set(params.requestId, { ...requests.get(params.requestId), url: params.request.url, method: params.request.method });
      if (!['GET', 'HEAD', 'OPTIONS'].includes(params.request.method)) mutations.push(params.request);
    }
    if (message.method === 'Network.requestWillBeSentExtraInfo') requests.set(params.requestId, { ...requests.get(params.requestId), headers: params.headers });
    if (message.method === 'Network.responseReceived' && params.type === 'Document') responses.push({ id: params.requestId, url: params.response.url, status: params.response.status });
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  const command = (method, params) => send(method, params, sessionId);
  const evaluate = async (expression) => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await command('Page.enable');
  await command('Runtime.enable');
  await command('Network.enable');
  await command('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });

  const clickFromOtherOrigin = async (path) => {
    const targetURL = `${privateOrigin}${path}`;
    await command('Page.navigate', { url: sourceURL });
    await until(() => evaluate(`location.href === ${JSON.stringify(sourceURL)} && document.readyState === 'complete'`), 'Cross-origin source page did not load');
    const point = await evaluate(`(() => { const link = document.createElement('a'); link.id = 'navigation-regression-link'; link.href = ${JSON.stringify(targetURL)}; link.textContent = 'Open private radio'; link.style.cssText = 'position:fixed;top:20px;left:20px;z-index:2147483647;background:white;color:black;padding:20px'; document.body.append(link); const rect = link.getBoundingClientRect(); return { x:rect.x+rect.width/2, y:rect.y+rect.height/2 }; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await command('Input.dispatchMouseEvent', { type, ...point, button: 'left', clickCount: 1 });
    await until(() => responses.some((response) => response.url === targetURL), `Clicked navigation did not receive ${path}`);
    const response = responses.filter((item) => item.url === targetURL).at(-1);
    await until(() => requests.get(response.id)?.headers, 'Navigation request headers were not captured');
    const request = requests.get(response.id);
    const header = (name) => Object.entries(request.headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];
    const result = { path, status: response.status, fetchSite: header('Sec-Fetch-Site'), fetchMode: header('Sec-Fetch-Mode'), fetchDest: header('Sec-Fetch-Dest'), fetchUser: header('Sec-Fetch-User') };
    assert.equal(result.fetchSite, 'cross-site');
    assert.equal(result.fetchMode, 'navigate');
    assert.equal(result.fetchDest, 'document');
    assert.equal(result.fetchUser, '?1');
    return result;
  };

  const oldEntry = await clickFromOtherOrigin('/api/genre-radio');
  assert.equal(oldEntry.status, 403);
  console.log('OLD_API_ENTRY', JSON.stringify(oldEntry));
  const pageEntry = await clickFromOtherOrigin(pagePath);
  assert.equal(pageEntry.status, 200);
  await until(() => evaluate(`Boolean(document.getElementById('cover')?.naturalWidth && Number.isFinite(document.getElementById('audio')?.duration) && document.getElementById('audio').duration > 0)`), 'New page did not load real cover and audio metadata', 30000);
  const player = await evaluate(`(() => { const audio = document.getElementById('audio'), cover = document.getElementById('cover'); return { path:location.pathname, title:document.getElementById('track-title').textContent, coverWidth:cover.naturalWidth, coverHeight:cover.naturalHeight, coverPath:new URL(cover.src).pathname, audioPath:new URL(audio.currentSrc).pathname, duration:audio.duration, paused:audio.paused, connection:document.getElementById('connection').textContent, width:innerWidth, scrollWidth:document.documentElement.scrollWidth }; })()`);
  assert.equal(player.path, pagePath === '/genre-radio' ? '/undiscovered' : pagePath);
  assert.ok(player.coverWidth >= 1024 && player.coverWidth <= 2048 && player.coverWidth % 16 === 0);
  assert.equal(player.coverHeight, player.coverWidth);
  assert.equal(await evaluate('document.title'), 'Undiscovered');
  assert.equal(player.coverPath, '/api/genre-radio/media');
  assert.equal(player.audioPath, '/api/genre-radio/media');
  assert.equal(player.paused, true);
  assert.ok(player.scrollWidth <= 390);
  const generationBefore = await evaluate(`document.getElementById('generation-status').textContent`);
  const audioBefore = await evaluate(`document.getElementById('audio').currentSrc`);
  const clickControl = async (id) => {
    const point = await evaluate(`(() => { const button = document.getElementById(${JSON.stringify(id)}); button.scrollIntoView({block:'center'}); const rect = button.getBoundingClientRect(); return {x:rect.x+rect.width/2,y:rect.y+rect.height/2}; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await command('Input.dispatchMouseEvent', { type, ...point, button: 'left', clickCount: 1 });
  };
  await clickControl('play');
  await until(() => evaluate(`!document.getElementById('audio').paused && document.getElementById('audio').currentTime > 0`), 'Audio-only Play failed');
  await clickControl('play');
  await until(() => evaluate(`document.getElementById('audio').paused`), 'Audio-only Pause failed');
  assert.equal(await evaluate(`document.getElementById('generation-status').textContent`), generationBefore);
  assert.equal(await evaluate(`document.getElementById('audio').currentSrc`), audioBefore);
  await clickControl('starred-toggle');
  await until(() => evaluate(`!document.getElementById('starred-panel').hidden && !document.getElementById('starred-list').textContent.includes('Loading')`), 'Starred library did not load');
  await clickControl('starred-toggle');
  await command('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  await clickControl('fullscreen-toggle');
  await until(() => evaluate(`document.querySelector('main.radio').classList.contains('immersive')`), 'Immersive player did not open');
  assert.equal(await evaluate(`getComputedStyle(document.getElementById('cover')).objectFit`), 'scale-down');
  await writeFile(join(output, 'immersive-player.png'), Buffer.from((await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })).data, 'base64'));
  await clickControl('fullscreen-toggle');
  await until(() => evaluate(`!document.querySelector('main.radio').classList.contains('immersive')`), 'Immersive player did not close');
  assert.equal(await evaluate(`document.getElementById('generation-status').textContent`), generationBefore);
  assert.equal(await evaluate(`document.getElementById('audio').currentSrc`), audioBefore);
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(blockedMutations.length, 0, 'Transport or library navigation attempted a server mutation');
  assert.equal(mutations.length, 0, 'Navigation and playback verification must not change the live radio');
  await evaluate('window.scrollTo(0,0)');
  const screenshot = join(output, 'clicked-page-mobile.png');
  await writeFile(screenshot, Buffer.from((await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })).data, 'base64'));
  console.log('NEW_PAGE_ENTRY', JSON.stringify({ navigation: pageEntry, player, mutationRequests: mutations.length, screenshot }));
} finally {
  socket?.close();
  browser.kill();
  await new Promise((resolve) => browser.exitCode !== null ? resolve() : browser.once('exit', resolve));
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
