const pagePath = location.pathname.replace(/\/$/, '');
const base = ['/undiscovered', '/genre-radio'].includes(pagePath) ? '/api/genre-radio' : pagePath;
const $ = (id) => document.getElementById(id);
const audio = $('audio');
let state = null;
let loadedTrackId = null;
let wantsPlayback = false;
let autoplayBlocked = false;
let playbackPending = false;
let generationPending = false;
let generationError = '';
let advancing = false;
let automaticAdvanceKey = '';
let localError = '';
const preferenceDrafts = {};
const preferenceVersions = { genre: 0, lyrics_id: 0 };
let preferenceSaves = Promise.resolve();
let pendingSaves = 0;
const preferenceErrors = {};
const submittedPreferences = {};
let lyricsPending = false;
let lyricsKey = '';
let genresKey = '';
let currentLyricsId = null;
let promptTrackId = null;
let playbackAttempt = 0;
let playbackIntentRevision = 0;
let selectedLibraryTrack = null;
let librarySelectionSequence = 0;
let librarySelectingId = null;
let stationPlayback = null;
let restoredPosition = null;
let libraryTracks = [];
let libraryMissingIds = [];
let libraryLoading = false;
let libraryLoaded = false;
let libraryError = '';
let librarySelectionError = '';
let librarySequence = 0;
let starredKey = '';
const starPending = new Set();
const starErrors = new Map();
let nextGenerationPending = false;
let nextGenerationError = '';
let lyricsRequestSequence = 0;
let immersive = false;
let nativeFullscreenActive = false;
let fullscreenSequence = 0;
let fullscreenFocus = null;
let fullscreenDesired = false;
let fullscreenScroll = { x: 0, y: 0 };
const player = document.querySelector('.radio');

function activeTrack() {
  return selectedLibraryTrack || state?.current;
}

function isStarred(id) {
  return Boolean(state?.starred_ids?.includes(id));
}

audio.volume = Number($('volume').value);
audio.loop = false;

function mediaURL(path) {
  if (typeof path !== 'string' || !path.startsWith('media?')) return '';
  return new URL(`${base}/${path}`, location.origin).href;
}

function sourceURL(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
  } catch {
    return '';
  }
}

async function request(path, body) {
  const response = await fetch(`${base}/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    cache: 'no-store',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error(`The server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}

function showError(error) {
  localError = error.name === 'TimeoutError' ? 'The server took too long to respond. Your current track is still available.' : error.message;
  renderPlayback();
}

function formatTime(value) {
  const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function setRangeFill(input) {
  const fraction = Number(input.max) > 0 ? Number(input.value) / Number(input.max) : 0;
  input.style.setProperty('--fill', `${fraction * 100}%`);
}

function renderTime() {
  const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
  $('seek').max = duration;
  $('seek').disabled = !duration;
  $('seek').value = audio.currentTime || 0;
  $('elapsed').textContent = formatTime(audio.currentTime);
  $('duration').textContent = formatTime(duration);
  $('seek').setAttribute('aria-valuetext', `${formatTime(audio.currentTime)} of ${formatTime(duration)}`);
  setRangeFill($('seek'));
  if (navigator.mediaSession?.setPositionState && duration > 0) {
    navigator.mediaSession.setPositionState({ duration, playbackRate: audio.playbackRate, position: Math.min(duration, Math.max(0, audio.currentTime)) });
  }
}

function shouldPause() {
  return !audio.paused || (wantsPlayback && !autoplayBlocked && (playbackPending || (!selectedLibraryTrack && (advancing || audio.ended)) || !activeTrack()));
}

function renderPlayback() {
  const playing = !audio.paused && !audio.ended;
  const track = activeTrack();
  const waitingForNext = !selectedLibraryTrack && wantsPlayback && track && audio.ended;
  const nextUnavailable = waitingForNext && localError;
  const active = shouldPause();
  const label = autoplayBlocked ? 'Tap Play' : active ? 'Pause' : 'Play';
  $('play-label').textContent = label;
  $('play-icon').textContent = active && !autoplayBlocked ? 'Ⅱ' : '▶';
  $('play').disabled = !track;
  $('play').setAttribute('aria-label', label);
  $('play').title = track ? `${label} audio` : 'Choose Start generation to make your first track';
  $('skip').disabled = Boolean(selectedLibraryTrack) || !state?.next || advancing;
  $('skip').title = selectedLibraryTrack ? 'Back to radio to use its queue' : state?.next ? 'Skip to next track' : 'The next track is not ready yet';
  $('library-mode').hidden = !selectedLibraryTrack;
  $('live-dot').dataset.playing = String(playing && !playbackPending);
  $('playback-status').textContent = autoplayBlocked ? 'Ready when you are' : waitingForNext ? nextUnavailable ? 'Next track unavailable' : 'Waiting for next track' : playbackPending ? 'Loading audio' : playing ? 'Now playing' : selectedLibraryTrack ? audio.ended ? 'Recording finished' : 'From your library' : track ? 'On your frequency' : 'Your own frequency';
  let note;
  if (autoplayBlocked) note = 'Your browser needs a tap. Press Play to hear this track.';
  else if (selectedLibraryTrack) note = audio.ended ? 'This recording has finished. Play it again, choose another starred recording, or go Back to radio.' : 'Playing from your library on this device. The radio queue and generation stay independent.';
  else if (waitingForNext) note = nextUnavailable ? 'The next track could not start. Playback stays at the end. Press Skip to retry a ready track, or Pause to stop waiting.' : `This track has finished. Playback continues when the next track is ready. ${state.running ? '' : 'Choose Start generation for more music. '}Press Pause to stop waiting.`;
  else if (playing) note = state.running ? 'The next track is made while you listen. Playback waits if it needs more time.' : 'Generation is paused. Playback continues through your ready queue.';
  else if (state?.current) note = 'Press Play to listen. Audio and generation are controlled separately.';
  else note = state?.running || state?.generating ? 'Your first track is being prepared. Press Play when it is ready; generation does not start audio.' : 'Choose Start generation to make your first track, then press Play to listen.';
  $('playback-note').textContent = note;
  $('generation-toggle').textContent = state?.running ? 'Pause generation' : 'Start generation';
  $('generation-toggle').disabled = !state || generationPending;
  $('generation-status').textContent = !state?.running ? state?.generating ? 'Finishing current generation' : 'Paused' : state.preparing ? 'Preparing' : state.generating ? 'Generating' : (state.queue?.length || 0) >= (state.buffer_target || 5) ? 'Buffer full' : 'Preparing';
  $('station-notice').textContent = state?.notice || '';
  $('station-notice').hidden = !state?.notice;
  const error = localError || nextGenerationError || generationError || state?.error || (state?.failure ? 'Generation failed. Your audio and library stay available.' : '');
  $('error-box').hidden = !error;
  $('error-message').textContent = error || '';
  $('retry').hidden = !(state?.error || state?.failure || generationError);
  $('retry').disabled = generationPending;
  $('failure-title').hidden = !state?.failure?.title;
  $('failure-title').textContent = state?.failure?.title ? `Failed song: ${state.failure.title}${state.failure.artist ? ` — ${state.failure.artist}` : ''}` : '';
  $('generate-another').hidden = !state?.failure?.can_skip;
  $('generate-another').disabled = nextGenerationPending;
  if (navigator.mediaSession) navigator.mediaSession.playbackState = playing && !playbackPending ? 'playing' : track ? 'paused' : 'none';
}

function setImage(id, path) {
  const image = $(id);
  const url = mediaURL(path);
  image.hidden = !url;
  if (url && image.src !== url) image.src = url;
  if (!url) image.removeAttribute('src');
}

function renderPlan(id, track) {
  const parts = [];
  if (Number.isFinite(track?.planned_duration) && track.planned_duration > 0) parts.push(`Planned length ${formatTime(track.planned_duration)}`);
  if (Number.isFinite(track?.bpm) && track.bpm > 0) parts.push(`${track.bpm} BPM`);
  const casting = { male: 'Vocals · Male', female: 'Vocals · Female', mixed: 'Vocals · Mixed', instrumental: 'Instrumental' }[track?.vocal_gender];
  if (casting) parts.push(casting);
  $(id).textContent = parts.join(' · ');
  $(id).hidden = !parts.length;
}

function renderCurrentSource() {
  const track = activeTrack();
  $('track-origin').textContent = track?.input_reused ? 'New version · saved lyrics' : 'Generated for you';
  const lyrics = state.lyrics.find((record) => record.id === track?.lyrics_id);
  const url = sourceURL(lyrics?.source_url);
  $('track-source').hidden = !track;
  $('track-source-label').textContent = url ? 'Source · ' : lyrics ? 'Source · Manually supplied' : 'Source · Not recorded';
  const link = $('track-source-link');
  link.hidden = !url;
  link.textContent = url ? `${lyrics.source_name || new URL(url).hostname} ↗` : '';
  if (url) link.href = url;
  else link.removeAttribute('href');
  renderPlan('track-plan', track);
}

function renderPrompts() {
  const track = activeTrack();
  const changedTrack = promptTrackId !== (track?.id || null);
  promptTrackId = track?.id || null;
  $('current-prompts').hidden = !track;
  if (changedTrack) {
    $('current-prompts').open = false;
    $('prompts-status').textContent = '';
  }
  for (const kind of ['music', 'cover']) {
    const prompt = track?.prompts?.[kind];
    const available = typeof prompt === 'string' && prompt.length > 0;
    const text = available ? prompt : 'Unavailable for this track.';
    if ($(`${kind}-prompt`).textContent !== text) {
      $(`${kind}-prompt`).textContent = text;
      $('prompts-status').textContent = '';
    }
    $(`copy-${kind}-prompt`).disabled = !available;
  }
}

async function copyPrompt(kind) {
  const id = activeTrack()?.id;
  const text = activeTrack()?.prompts?.[kind];
  if (typeof text !== 'string' || !text.length) return;
  $('prompts-status').textContent = '';
  let message;
  try {
    await navigator.clipboard.writeText(text);
    message = `${kind === 'music' ? 'Music' : 'Cover'} prompt copied.`;
  } catch {
    message = 'Could not copy. Select the prompt text and copy it manually.';
  }
  if (activeTrack()?.id === id && activeTrack()?.prompts?.[kind] === text) $('prompts-status').textContent = message;
}

function renderNext() {
  const next = state.next;
  const job = state.generating;
  renderPlan('next-plan', next || job);
  const stage = { queued: 'Queued', gpu: 'Checking GPU memory', prompt: 'Writing the sound', qwen: 'Writing the sound', cover: 'Creating cover art', krea: 'Creating cover art', audio: 'Generating music', music: 'Generating music', minimax: 'Generating music' }[job?.stage] || job?.stage || 'Preparing';
  $('buffer-count').textContent = `${state.queue?.length || 0} / ${state.buffer_target || 5} ready`;
  $('buffer-stage').textContent = next && job ? `${job.genre ? `${job.genre} · ` : ''}${stage}${typeof job.progress === 'number' ? ` · ${Math.round(Math.min(1, Math.max(0, job.progress)) * 100)}%` : ''}` : next && state.preparing ? 'Preparing inputs' : '';
  setImage('next-cover', next?.cover_url);
  $('next-placeholder').hidden = Boolean(next);
  $('generation-progress').hidden = true;
  if (next) {
    $('next-title').textContent = next.title;
    $('next-detail').textContent = `${next.genre} · ${formatTime(next.duration)} · ready after this track`;
    $('next-badge').textContent = 'Ready';
  } else if (state.preparing) {
    $('next-title').textContent = 'Preparing next track';
    $('next-detail').textContent = 'Preparing inputs';
    $('next-badge').textContent = 'Preparing';
  } else if (job) {
    $('next-title').textContent = job.title || 'Making a new track';
    const progress = typeof job.progress === 'number' && Number.isFinite(job.progress) ? Math.min(1, Math.max(0, job.progress)) : null;
    $('next-detail').textContent = `${job.genre ? `${job.genre} · ` : ''}${stage}${progress === null ? '' : ` · ${Math.round(progress * 100)}%`}`;
    $('next-badge').textContent = 'Creating';
    if (progress !== null) {
      $('generation-progress').hidden = false;
      $('generation-progress').value = progress;
    }
  } else {
    $('next-title').textContent = state.error ? 'Next track needs a retry' : state.running ? 'Preparing the next direction' : 'A fresh direction';
    $('next-detail').textContent = state.error ? 'Your current track stays available.' : state.running ? 'Waiting for generation to begin.' : 'A few songs ahead, never a fixed playlist.';
    $('next-badge').textContent = state.error ? 'Paused' : 'Waiting';
  }
}

function renderPreferences() {
  const groups = state.genre_groups;
  const genres = groups ? groups.flatMap((group) => group.genres) : state.genres;
  const nextGenresKey = JSON.stringify(groups || genres);
  if (genresKey !== nextGenresKey) {
    genresKey = nextGenresKey;
    const selected = $('genre').value;
    const options = groups ? groups.map((group) => {
      const optgroup = document.createElement('optgroup');
      optgroup.label = group.label;
      optgroup.append(...group.genres.filter((genre) => genre !== 'Auto').map((genre) => new Option(genre, genre)));
      return optgroup;
    }) : genres.filter((genre) => genre !== 'Auto').map((genre) => new Option(genre, genre));
    $('genre').replaceChildren(new Option('Auto · Qwen chooses', 'Auto'), ...options, new Option('Custom genre…', '__custom__'));
    $('genre').value = selected;
  }
  const nextLyricsKey = JSON.stringify(state.lyrics);
  if (lyricsKey !== nextLyricsKey) {
    lyricsKey = nextLyricsKey;
    const selected = $('lyrics-choice').value;
    $('lyrics-choice').replaceChildren(new Option('Auto · Fresh + saved songs', 'auto'), ...state.lyrics.map((lyrics) => new Option(`${lyrics.title}${lyrics.artist ? ` — ${lyrics.artist}` : ''}`, lyrics.id)));
    $('lyrics-choice').value = selected;
  }
  if (!Object.hasOwn(preferenceDrafts, 'genre')) {
    const genre = state.preferences.genre;
    const custom = genre !== 'Auto' && !genres.includes(genre);
    $('genre').value = custom ? '__custom__' : genre;
    $('custom-genre').value = custom ? genre : '';
    showCustomGenre();
  }
  $('lyrics-choice').value = preferenceDrafts.lyrics_id ?? state.preferences.lyrics_id;
  $('preferences-fields').disabled = false;
  $('lyrics-fields').disabled = lyricsPending;
}

function renderFullscreen() {
  player.classList.toggle('immersive', immersive);
  $('fullscreen-toggle').textContent = immersive ? 'Exit fullscreen' : 'Fullscreen';
  $('fullscreen-toggle').setAttribute('aria-pressed', String(immersive));
  $('cover-frame').setAttribute('aria-label', immersive ? 'Exit fullscreen player' : 'Enter fullscreen player');
  if (!immersive) {
    player.classList.remove('show-secondary');
    $('immersive-controls').setAttribute('aria-expanded', 'false');
    $('immersive-controls').textContent = 'Show controls';
    window.scrollTo(fullscreenScroll.x, fullscreenScroll.y);
  }
}

async function setFullscreen(enabled) {
  const sequence = ++fullscreenSequence;
  fullscreenDesired = enabled;
  nativeFullscreenActive = false;
  if (enabled) {
    fullscreenFocus = document.activeElement;
    fullscreenScroll = { x: window.scrollX, y: window.scrollY };
  }
  immersive = enabled;
  renderFullscreen();
  if (enabled) { window.scrollTo(0, 0); player.scrollTop = 0; }
  if (enabled) {
    try {
      if (player.requestFullscreen) await player.requestFullscreen();
      if (sequence === fullscreenSequence) nativeFullscreenActive = document.fullscreenElement === player;
      if (sequence !== fullscreenSequence && document.fullscreenElement === player && !fullscreenDesired) await document.exitFullscreen();
    } catch {}
  } else {
    try { if (document.fullscreenElement === player) await document.exitFullscreen(); } catch {}
    if (sequence === fullscreenSequence) fullscreenFocus?.focus({ preventScroll: true });
  }
}

document.addEventListener('fullscreenchange', () => {
  if (document.fullscreenElement === player) {
    nativeFullscreenActive = true;
    immersive = true;
  } else if (nativeFullscreenActive) {
    nativeFullscreenActive = false;
    immersive = false;
    fullscreenDesired = false;
    fullscreenSequence++;
    fullscreenFocus?.focus({ preventScroll: true });
  }
  renderFullscreen();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && immersive) {
    event.preventDefault();
    void setFullscreen(false);
  }
});
$('fullscreen-toggle').addEventListener('click', () => void setFullscreen(!immersive));
$('cover-frame').addEventListener('click', () => void setFullscreen(!immersive));
$('immersive-controls').addEventListener('click', () => {
  const shown = player.classList.toggle('show-secondary');
  $('immersive-controls').setAttribute('aria-expanded', String(shown));
  $('immersive-controls').textContent = shown ? 'Hide controls' : 'Show controls';
});

function renderStar() {
  const track = activeTrack();
  const starred = isStarred(track?.id);
  $('star-track').disabled = !track || starPending.has(track.id);
  $('star-track').setAttribute('aria-pressed', String(starred));
  $('star-track').setAttribute('aria-label', starred ? 'Unstar this recording' : 'Star this recording');
  $('star-icon').textContent = starred ? '★' : '☆';
  $('star-label').textContent = starred ? 'Starred' : 'Star';
  const message = starErrors.get(track?.id) || (starPending.has(track?.id) ? 'Saving star…' : '');
  $('star-status').hidden = !message;
  $('star-status').textContent = message;
}

function renderLibrary() {
  const tracks = libraryTracks.filter((track) => isStarred(track.id));
  const missingIds = libraryMissingIds.filter(isStarred);
  $('refresh-starred').disabled = libraryLoading;
  $('starred-status').textContent = [libraryLoading ? 'Loading starred recordings…' : libraryError || (!tracks.length && !missingIds.length ? 'No starred recordings yet. Star a recording to save it here.' : ''), missingIds.length ? `${missingIds.length} starred recording${missingIds.length === 1 ? '' : 's'} unavailable: media or metadata is missing. You can remove the saved star below.` : '', librarySelectionError, librarySelectingId ? 'Loading recording… Your current audio stays available.' : ''].filter(Boolean).join(' ');
  const entries = [...tracks, ...missingIds.map((id) => ({ id, missing: true }))];
  $('starred-list').replaceChildren(...entries.map((track) => {
    const item = document.createElement('li');
    item.className = 'starred-item';
    item.dataset.trackId = track.id;
    const art = document.createElement('div');
    art.className = 'library-art';
    const unavailable = document.createElement('span');
    unavailable.textContent = 'No cover';
    art.append(unavailable);
    const url = mediaURL(track.cover_url);
    if (url) {
      const image = document.createElement('img');
      image.alt = '';
      image.src = url;
      image.addEventListener('load', () => { unavailable.hidden = true; });
      image.addEventListener('error', () => { image.remove(); unavailable.hidden = false; });
      art.append(image);
    }
    const copy = document.createElement('div');
    copy.className = 'library-copy';
    const title = document.createElement('h3');
    title.textContent = track.missing ? `Unavailable recording · ${track.id}` : track.title;
    const detail = document.createElement('p');
    detail.textContent = track.missing ? 'The star is saved, but this recording cannot be played.' : [track.artist || 'Artist not recorded', track.genre || 'Genre not recorded', Number.isFinite(track.duration) ? formatTime(track.duration) : 'Duration unavailable'].join(' · ');
    copy.append(title, detail);
    if (starErrors.has(track.id)) {
      const error = document.createElement('p');
      error.textContent = starErrors.get(track.id);
      error.setAttribute('role', 'status');
      copy.append(error);
    }
    const actions = document.createElement('div');
    actions.className = 'library-actions';
    const play = document.createElement('button');
    play.className = 'secondary-button';
    play.type = 'button';
    play.textContent = librarySelectingId === track.id ? 'Loading…' : 'Play';
    play.setAttribute('aria-label', `Play ${track.title || 'unavailable recording'}`);
    play.disabled = track.missing || !mediaURL(track.audio_url) || librarySelectingId === track.id;
    play.addEventListener('click', () => void selectLibraryTrack(track.id));
    const unstar = document.createElement('button');
    unstar.className = 'text-button';
    unstar.type = 'button';
    unstar.textContent = 'Unstar';
    unstar.setAttribute('aria-label', `Unstar ${track.title || 'unavailable recording'}`);
    unstar.disabled = starPending.has(track.id);
    unstar.addEventListener('click', () => void setStar(track.id, false));
    actions.append(play, unstar);
    item.append(art, copy, actions);
    return item;
  }));
}

async function loadLibrary() {
  const sequence = ++librarySequence;
  libraryLoading = true;
  libraryError = '';
  renderLibrary();
  try {
    const result = await request('favorites');
    if (sequence !== librarySequence) return;
    libraryTracks = result.tracks;
    libraryMissingIds = result.missing_ids;
    libraryLoaded = true;
  } catch (error) {
    if (sequence !== librarySequence) return;
    libraryError = `Could not load starred recordings: ${error.message} Use Refresh library to retry.`;
  } finally {
    if (sequence === librarySequence) {
      libraryLoading = false;
      renderLibrary();
    }
  }
}

async function setStar(id, starred) {
  if (!id || starPending.has(id) || isStarred(id) === starred) return;
  starPending.add(id);
  starErrors.delete(id);
  renderStar();
  renderLibrary();
  const instance = state.instance_id;
  try {
    const result = await request('favorites', { id, starred });
    applyState(result.state);
  } catch (error) {
    if (state.instance_id === instance && isStarred(id) !== starred) starErrors.set(id, `Could not ${starred ? 'save' : 'remove'} star: ${error.message}`);
  } finally {
    starPending.delete(id);
    renderStar();
    renderLibrary();
  }
}

async function selectLibraryTrack(id) {
  const sequence = ++librarySelectionSequence;
  const intentRevision = playbackIntentRevision;
  librarySelectingId = id;
  librarySelectionError = '';
  renderLibrary();
  try {
    const result = await request(`track?id=${encodeURIComponent(id)}`);
    if (sequence !== librarySelectionSequence) return;
    if (result.track?.id !== id || !mediaURL(result.track.audio_url)) throw new Error('This recording has no playable audio.');
    if (!selectedLibraryTrack) stationPlayback = { id: state?.current?.id, time: audio.currentTime, wantsPlayback, ended: audio.ended };
    const alreadyLoaded = selectedLibraryTrack?.id === id;
    selectedLibraryTrack = result.track;
    if (intentRevision === playbackIntentRevision) wantsPlayback = true;
    restoredPosition = null;
    renderActiveTrack();
    if (alreadyLoaded) void playAudio();
  } catch (error) {
    if (sequence !== librarySelectionSequence) return;
    librarySelectionError = `Could not play recording: ${error.message} Choose Play to retry.`;
  } finally {
    if (sequence === librarySelectionSequence) {
      librarySelectingId = null;
      renderLibrary();
    }
  }
}

function backToRadio() {
  librarySelectionSequence++;
  librarySelectingId = null;
  librarySelectionError = '';
  if (selectedLibraryTrack) {
    selectedLibraryTrack = null;
    wantsPlayback = Boolean(stationPlayback?.wantsPlayback);
    restoredPosition = stationPlayback?.id === state?.current?.id ? { id: stationPlayback.id, time: stationPlayback.time, ended: stationPlayback.ended } : null;
    const restoreEnded = restoredPosition?.ended;
    stationPlayback = null;
    renderActiveTrack();
    if (restoreEnded) automaticAdvanceKey = JSON.stringify([state.instance_id, loadedTrackId, state.next?.id]);
  }
  renderLibrary();
}

function renderActiveTrack() {
  const track = activeTrack();
  const key = track ? `${selectedLibraryTrack ? 'library' : 'radio'}:${track.id}` : null;
  if (key !== loadedTrackId) {
    if (restoredPosition?.id !== track?.id) restoredPosition = null;
    loadedTrackId = key;
    playbackAttempt++;
    lyricsRequestSequence++;
    playbackPending = false;
    autoplayBlocked = false;
    automaticAdvanceKey = '';
    localError = '';
    currentLyricsId = null;
    $('current-lyrics').open = false;
    $('current-lyrics-text').textContent = '';
    $('current-lyrics-source').hidden = true;
    audio.pause();
    if (track) audio.src = mediaURL(track.audio_url);
    else { audio.removeAttribute('src'); audio.load(); }
    $('track-title').textContent = track?.title || 'Find your next sound.';
    $('track-meta').textContent = track ? track.artist || 'An original generated track' : 'Your lyrics. A new genre. An original cover.';
    $('track-genre').textContent = track?.genre || 'Open to anything';
    $('cover').alt = track ? `Generated cover for ${track.title}` : '';
    $('cover-caption').hidden = Boolean(mediaURL(track?.cover_url));
    $('cover-caption').textContent = track ? 'Cover unavailable' : 'A little room for discovery.';
    setImage('cover', track?.cover_url);
    $('cover-frame').disabled = !mediaURL(track?.cover_url);
    $('current-lyrics').hidden = !track?.lyrics_id;
    if (navigator.mediaSession && 'MediaMetadata' in window) navigator.mediaSession.metadata = track ? new MediaMetadata({ title: track.title, artist: track.artist || 'Undiscovered', album: track.genre, artwork: mediaURL(track.cover_url) ? [{ src: mediaURL(track.cover_url) }] : [] }) : null;
    renderTime();
    if (wantsPlayback && !restoredPosition) void playAudio();
  }
  renderCurrentSource();
  renderPrompts();
  renderStar();
  renderPlayback();
}

function applyState(snapshot, source = 'http') {
  if (state && snapshot.instance_id !== state.instance_id && source !== 'event') return;
  if (state && snapshot.instance_id === state.instance_id && snapshot.version < state.version) return;
  if (state?.failure?.id !== snapshot.failure?.id) nextGenerationError = '';
  state = snapshot;
  if (!activeTrack() && state.error) wantsPlayback = false;
  renderActiveTrack();
  renderNext();
  renderPreferences();
  const key = JSON.stringify([state.instance_id, [...(state.starred_ids || [])].sort()]);
  if (key !== starredKey) {
    starredKey = key;
    librarySequence++;
    libraryLoading = false;
    libraryLoaded = false;
    if (!$('starred-panel').hidden) void loadLibrary();
  }
  if (!selectedLibraryTrack && wantsPlayback && audio.ended && state.next) void advance(true);
}

async function playAudio() {
  if (!activeTrack() || !wantsPlayback) return;
  const attempt = ++playbackAttempt;
  autoplayBlocked = false;
  playbackPending = true;
  renderPlayback();
  try {
    await audio.play();
    if (attempt !== playbackAttempt) return;
    playbackPending = false;
  } catch (error) {
    if (attempt !== playbackAttempt || !wantsPlayback) return;
    playbackPending = false;
    if (error.name === 'NotAllowedError') autoplayBlocked = true;
    else {
      wantsPlayback = false;
      showError(new Error(`Audio could not play: ${error.message}`));
    }
  }
  renderPlayback();
}

function startPlayback() {
  if (!activeTrack()) return;
  playbackIntentRevision++;
  localError = '';
  wantsPlayback = true;
  if (audio.error) audio.load();
  void playAudio();
}

function pausePlayback() {
  playbackIntentRevision++;
  wantsPlayback = false;
  autoplayBlocked = false;
  playbackPending = false;
  playbackAttempt++;
  audio.pause();
  localError = '';
  renderPlayback();
}

async function setGeneration(running) {
  if (generationPending || !state) return;
  generationPending = true;
  generationError = '';
  renderPlayback();
  try {
    applyState(await request(running ? 'start' : 'stop', {}));
  } catch (error) {
    generationError = `Generation could not ${running ? 'start' : 'pause'}: ${error.message}`;
    if (running && !state.lyrics.length) openLyricsControls();
  } finally {
    generationPending = false;
    renderPlayback();
  }
}

async function advance(ended = false) {
  if (selectedLibraryTrack || advancing || !state?.next || (ended && (!wantsPlayback || !audio.ended))) return;
  const key = JSON.stringify([state.instance_id, loadedTrackId, state.next.id]);
  if (ended && automaticAdvanceKey === key) return;
  automaticAdvanceKey = key;
  advancing = true;
  localError = '';
  const previousId = loadedTrackId;
  renderPlayback();
  try {
    const result = await request('advance', {});
    applyState(result.state);
  } catch (error) {
    if (previousId === loadedTrackId) showError(error);
  } finally {
    advancing = false;
    renderPlayback();
    if (previousId === loadedTrackId) void advance(true);
  }
}

async function generateAnother() {
  const failure = state?.failure;
  if (!failure?.can_skip || nextGenerationPending) return;
  const instance = state.instance_id;
  nextGenerationPending = true;
  nextGenerationError = '';
  renderPlayback();
  try {
    const result = await request('generation/next', { failed_id: failure.id });
    applyState(result.state);
  } catch (error) {
    if (state.instance_id === instance && state.failure?.id === failure.id) nextGenerationError = `Could not move on: ${error.message}`;
  } finally {
    nextGenerationPending = false;
    renderPlayback();
  }
}

function openLyricsControls() {
  $('next-panel').open = true;
  $('paste-details').open = true;
  $('lyrics-title').focus({ preventScroll: true });
}

function showCustomGenre() {
  const custom = $('genre').value === '__custom__';
  $('custom-genre-field').hidden = !custom;
  $('custom-genre').required = custom;
}

$('play').addEventListener('click', () => shouldPause() ? pausePlayback() : startPlayback());
$('generation-toggle').addEventListener('click', () => void setGeneration(!state.running));
$('retry').addEventListener('click', () => void setGeneration(true));
$('generate-another').addEventListener('click', () => void generateAnother());
$('star-track').addEventListener('click', () => void setStar(activeTrack()?.id, !isStarred(activeTrack()?.id)));
$('starred-toggle').addEventListener('click', () => {
  const open = $('starred-panel').hidden;
  $('starred-panel').hidden = !open;
  $('starred-toggle').setAttribute('aria-expanded', String(open));
  if (open && !libraryLoaded && !libraryLoading) void loadLibrary();
});
$('refresh-starred').addEventListener('click', () => void loadLibrary());
$('back-to-radio').addEventListener('click', backToRadio);
$('skip').addEventListener('click', () => void advance());
$('seek').addEventListener('input', () => { audio.currentTime = Number($('seek').value); renderTime(); });
$('volume').addEventListener('input', () => { audio.volume = Number($('volume').value); setRangeFill($('volume')); });
$('next-panel').addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || !$('next-panel').open) return;
  event.preventDefault();
  $('next-panel').open = false;
  $('controls-toggle').focus({ preventScroll: true });
});
for (const kind of ['music', 'cover']) $(`copy-${kind}-prompt`).addEventListener('click', () => void copyPrompt(kind));
function renderPreferenceStatus() {
  const errors = Object.values(preferenceErrors);
  $('preferences-status').textContent = pendingSaves ? 'Saving…' : errors.length ? `Error: ${errors.join(' ')} Change the setting or leave the genre field to retry.` : Object.hasOwn(preferenceDrafts, 'genre') ? 'Enter a genre. It saves when you leave the field.' : 'Saved for future generations.';
}

function draftPreference(field, value) {
  preferenceDrafts[field] = value;
  preferenceVersions[field]++;
  renderPreferenceStatus();
}

function savePreference(field, value) {
  if (field === 'genre' && (!value || value === '__custom__')) return Promise.resolve(false);
  if (!Object.hasOwn(preferenceDrafts, field) || preferenceDrafts[field] !== value) draftPreference(field, value);
  const version = preferenceVersions[field];
  if (submittedPreferences[field]?.version === version) return submittedPreferences[field].promise;
  pendingSaves++;
  delete preferenceErrors[field];
  renderPreferenceStatus();
  const promise = preferenceSaves.then(async () => {
    try {
      applyState(await request('preferences', { [field]: value }));
      if (preferenceVersions[field] === version) delete preferenceDrafts[field];
      return true;
    } catch (error) {
      if (preferenceVersions[field] === version) {
        preferenceErrors[field] = error.message;
        delete submittedPreferences[field];
      }
      return false;
    } finally {
      pendingSaves--;
      renderPreferences();
      renderPreferenceStatus();
    }
  });
  submittedPreferences[field] = { version, promise };
  preferenceSaves = promise;
  return promise;
}

$('genre').addEventListener('change', () => {
  showCustomGenre();
  const value = $('genre').value === '__custom__' ? $('custom-genre').value.trim() : $('genre').value;
  draftPreference('genre', value);
  if ($('genre').value !== '__custom__') void savePreference('genre', value);
});
$('custom-genre').addEventListener('input', () => draftPreference('genre', $('custom-genre').value.trim()));
for (const event of ['change', 'blur']) $('custom-genre').addEventListener(event, () => {
  if (Object.hasOwn(preferenceDrafts, 'genre')) void savePreference('genre', $('custom-genre').value.trim());
});
$('lyrics-choice').addEventListener('change', () => {
  draftPreference('lyrics_id', $('lyrics-choice').value);
  void savePreference('lyrics_id', $('lyrics-choice').value);
});
$('preferences-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if ($('genre').value === '__custom__') void savePreference('genre', $('custom-genre').value.trim());
});

$('lyrics-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (lyricsPending) return;
  lyricsPending = true;
  $('lyrics-status').textContent = 'Saving…';
  const sourceVersion = preferenceVersions.lyrics_id;
  renderPreferences();
  try {
    const result = await request('lyrics', { title: $('lyrics-title').value.trim(), artist: $('lyrics-artist').value.trim(), text: $('lyrics-text').value });
    applyState(result.state);
    $('lyrics-form').reset();
    if (sourceVersion !== preferenceVersions.lyrics_id) {
      $('lyrics-status').textContent = 'Lyrics saved. Your newer Source choice is unchanged.';
      return;
    }
    $('lyrics-status').textContent = 'Lyrics saved. Selecting…';
    const selection = savePreference('lyrics_id', result.lyrics.id);
    const selectionVersion = preferenceVersions.lyrics_id;
    const selected = await selection;
    $('lyrics-status').textContent = preferenceVersions.lyrics_id !== selectionVersion ? 'Lyrics saved. Your newer Source choice is unchanged.' : selected ? 'Lyrics saved and selected for future generation. Uses this song once, then returns to Auto.' : 'Lyrics are saved, but could not be selected. Choose them from the Source menu.';
  } catch (error) {
    $('lyrics-status').textContent = `Could not save. Your text is still here. ${error.message}`;
  } finally {
    lyricsPending = false;
    renderPreferences();
  }
});

$('current-lyrics').addEventListener('toggle', async () => {
  const id = activeTrack()?.lyrics_id;
  if (!$('current-lyrics').open || !id || currentLyricsId === id) return;
  const sequence = ++lyricsRequestSequence;
  currentLyricsId = id;
  $('current-lyrics-text').textContent = 'Loading lyrics…';
  try {
    const lyrics = await request(`lyrics?id=${encodeURIComponent(id)}`);
    if (sequence !== lyricsRequestSequence || activeTrack()?.lyrics_id !== id) return;
    $('current-lyrics-text').textContent = lyrics.text;
    $('current-lyrics-meta').textContent = `${lyrics.title}${lyrics.artist ? ` — ${lyrics.artist}` : ''}`;
    const url = sourceURL(lyrics.source_url);
    $('current-lyrics-source').hidden = !url;
    $('current-lyrics-source').textContent = url ? `${lyrics.source_name || new URL(url).hostname} ↗` : '';
    if (url) $('current-lyrics-source').href = url;
    else $('current-lyrics-source').removeAttribute('href');
  } catch (error) {
    if (sequence !== lyricsRequestSequence || activeTrack()?.lyrics_id !== id) return;
    currentLyricsId = null;
    $('current-lyrics-text').textContent = `Could not load lyrics: ${error.message}. Close and reopen to retry.`;
  }
});

for (const event of ['timeupdate', 'loadedmetadata', 'durationchange', 'seeked', 'ratechange']) audio.addEventListener(event, renderTime);
for (const event of ['playing', 'pause', 'ended', 'seeked']) audio.addEventListener(event, renderPlayback);
audio.addEventListener('playing', () => { automaticAdvanceKey = ''; playbackPending = false; renderPlayback(); });
audio.addEventListener('waiting', () => { if (wantsPlayback) playbackPending = true; renderPlayback(); });
audio.addEventListener('ended', () => {
  if (selectedLibraryTrack) { wantsPlayback = false; renderPlayback(); }
  else void advance(true);
});
audio.addEventListener('loadedmetadata', () => {
  if (!restoredPosition || selectedLibraryTrack || restoredPosition.id !== activeTrack()?.id) return;
  const position = restoredPosition;
  restoredPosition = null;
  audio.currentTime = Math.min(Number.isFinite(audio.duration) ? audio.duration : position.time, position.time);
  renderTime();
  if (wantsPlayback && !position.ended) void playAudio();
});
audio.addEventListener('error', () => { playbackPending = false; wantsPlayback = false; showError(new Error(selectedLibraryTrack ? 'This recording’s audio could not be loaded. Press Play to retry, choose another recording, or go Back to radio.' : 'This audio could not be loaded. Press Play to retry, or skip when the next track is ready.')); });
audio.addEventListener('volumechange', () => { $('volume').value = audio.volume; setRangeFill($('volume')); });
$('cover').addEventListener('error', () => { $('cover').hidden = true; $('cover-caption').hidden = false; $('cover-caption').textContent = 'Cover unavailable'; });
$('next-cover').addEventListener('error', () => { $('next-cover').hidden = true; $('next-placeholder').hidden = false; });

if (navigator.mediaSession) {
  const seekBy = (seconds) => { if (Number.isFinite(audio.duration)) { audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + seconds)); renderTime(); } };
  for (const [action, handler] of Object.entries({ play: startPlayback, pause: pausePlayback, stop: pausePlayback, nexttrack: () => void advance(), seekbackward: (event) => seekBy(-(event.seekOffset || 10)), seekforward: (event) => seekBy(event.seekOffset || 10), seekto: (event) => seekBy(event.seekTime - audio.currentTime) })) {
    try { navigator.mediaSession.setActionHandler(action, handler); } catch {}
  }
}

setRangeFill($('volume'));
let receivedEvent = false;
const events = new EventSource(`${base}/events`);
events.onopen = () => { $('connection').textContent = 'Private radio'; $('connection').dataset.connected = 'true'; };
events.onerror = () => { $('connection').textContent = 'Reconnecting… audio stays available'; $('connection').dataset.connected = 'false'; };
events.onmessage = (event) => {
  try {
    applyState(JSON.parse(event.data), 'event');
    receivedEvent = true;
  } catch {
    showError(new Error('The server sent an unreadable update. Waiting for reconnection.'));
  }
};
request('state').then((snapshot) => { if (!receivedEvent) applyState(snapshot); }).catch(showError);
