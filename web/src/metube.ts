import {api, button, empty, h, json, labelled, navigate, notify} from './core.js';

interface Format {value: string; label: string; qualities: string[]}
interface Formats {video: Format[]; audio: Format[]}
interface DownloadItem {
  id: string; title: string; status: string; media: string; format: string; quality: string;
  percent: number | null; speed: number | null; eta: number | null; size: number | null;
  timestamp: number | null; error: string; filename?: string;
  can_retry: boolean; can_cancel: boolean; can_start: boolean; can_open: boolean; can_open_folder: boolean;
}
interface Snapshot {queue: DownloadItem[]; pending: DownloadItem[]; done: DownloadItem[]}
interface Row {element: HTMLElement; title: HTMLElement; badge: HTMLElement; detail: HTMLElement;
  progress: HTMLProgressElement; error: HTMLElement; actions: HTMLElement; signature: string; actionSignature: string}

const statusLabels: Record<string, string> = {pending: 'Waiting to start', queued: 'Queued', preparing: 'Preparing',
  downloading: 'Downloading', postprocessing: 'Processing', finished: 'Completed', error: 'Failed', scheduled: 'Scheduled', unknown: 'Unknown'};

function bytes(value: number): string {
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}
function duration(value: number): string {
  const seconds = Math.max(0, Math.ceil(value));
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` :
    seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

/** Native MeTube API client. Each route owns its polling and tears it down on exit. */
export async function renderMeTube(host: HTMLElement): Promise<() => void> {
  let disposed = false, polling = false, online = false, starting = false, submitting = false;
  let startingAt = 0, timer: ReturnType<typeof setTimeout> | undefined;
  let request: AbortController | undefined;
  let formats: Formats | undefined;
  let latest: Snapshot = {queue: [], pending: [], done: []};
  let historyLimit = 40;
  const rows = new Map<string, Row>();
  const pendingActions = new Set<string>();
  const message = h('p', {className: 'metube-message', role: 'status', 'aria-live': 'polite'});
  const serviceMessage = h('p', {className: 'metube-service-detail'}, 'Connecting to your local MeTube service…');
  const live = h('span', {className: 'metube-live'}, 'Connecting');
  const media = h('select', {'aria-label': 'Media type'}, h('option', {value: 'video'}, 'Video'), h('option', {value: 'audio'}, 'Audio'));
  const format = h('select', {'aria-label': 'Format', disabled: true});
  const quality = h('select', {'aria-label': 'Quality', disabled: true});
  const url = h('input', {type: 'url', required: true, placeholder: 'Paste a video, audio or playlist URL',
    'aria-label': 'Media URL', autocomplete: 'off', spellcheck: false, maxLength: 8192});
  const submit = h('button', {type: 'submit', className: 'primary', disabled: true}, 'Add download');
  const paste = button('Paste', async () => {
    try {
      if (!navigator.clipboard?.readText) throw new Error('Clipboard unavailable');
      const value = (await navigator.clipboard.readText()).trim();
      if (disposed) return;
      if (value) {url.value = value; message.textContent = '';}
      else message.textContent = 'Your clipboard is empty. Copy a media URL, then paste it here.';
    } catch {
      if (!disposed) message.textContent = 'Clipboard access is unavailable. Focus the URL field and press Ctrl+V to paste.';
    }
    if (!disposed) url.focus();
  });
  const start = button('Start MeTube', async () => {
    starting = true; startingAt = Date.now(); setState();
    try {
      await api('/api/services/metube/start', json('POST', {}));
      if (disposed) return;
      serviceMessage.textContent = 'Starting Docker and MeTube. The first start may take a few minutes.';
      schedule(1200);
    } catch (error) {
      if (disposed) return;
      starting = false; serviceMessage.textContent = error instanceof Error ? error.message : String(error); setState();
    }
  });
  const refresh = button('Refresh', () => {schedule(0);});
  const service = h('div', {className: 'metube-service'}, serviceMessage, start);
  const form = h('form', {className: 'panel metube-form'},
    h('h2', {}, 'New download'),
    labelled('Media URL', h('div', {className: 'metube-url'}, url, paste)),
    h('div', {className: 'metube-options'}, labelled('Type', media), labelled('Format', format), labelled('Quality', quality), submit),
    message);
  const queueCount = h('span', {className: 'metube-count'}, '0');
  const historyCount = h('span', {className: 'metube-count'}, '0');
  const queueList = h('div', {className: 'metube-list'});
  const historyList = h('div', {className: 'metube-list'});
  const filter = h('input', {type: 'search', placeholder: 'Search download history', 'aria-label': 'Search download history'});
  const showMore = button('Show more downloads', () => {historyLimit += 40; renderLists();});
  const page = h('section', {className: 'metube-page', 'aria-label': 'MeTube downloads'},
    h('header', {className: 'metube-heading'}, h('div', {}, h('h1', {}, 'MeTube'),
      h('p', {className: 'muted'}, 'Download video and audio to your local collection.')),
      h('div', {className: 'toolbar'}, live, refresh)), service, form,
    h('section', {className: 'panel metube-section', 'aria-label': 'Download queue'},
      h('div', {className: 'metube-section-heading'}, h('h2', {}, 'Queue ', queueCount)), queueList),
    h('section', {className: 'panel metube-section', 'aria-label': 'Download history'},
      h('div', {className: 'metube-section-heading'}, h('h2', {}, 'History ', historyCount), filter), historyList, showMore));
  host.replaceChildren(page);
  page.addEventListener('contextmenu', event => {event.preventDefault(); navigate('tools');});

  function setState(): void {
    live.textContent = online ? '● Connected' : starting ? '◌ Starting' : '○ Offline';
    live.className = `metube-live ${online ? 'connected' : starting ? 'starting' : 'offline'}`;
    service.hidden = online;
    start.disabled = starting;
    start.textContent = starting ? 'Starting…' : 'Start MeTube';
    submit.disabled = submitting || !online || !formats;
    submit.textContent = submitting ? 'Submitting…' : 'Add download';
    refresh.disabled = polling;
  }
  function setQualities(): void {
    const options = formats?.[media.value as keyof Formats].find(item => item.value === format.value)?.qualities || [];
    const selected = quality.value;
    quality.replaceChildren(...options.map(value => h('option', {value}, value === 'best' ? 'Best available' :
      value === 'worst' ? 'Smallest available' : media.value === 'audio' ? `${value} kbps` : `${value}p`)));
    if (options.includes(selected)) quality.value = selected;
    quality.disabled = !options.length;
  }
  function setFormats(): void {
    const options = formats?.[media.value as keyof Formats] || [];
    format.replaceChildren(...options.map(item => h('option', {value: item.value}, item.label)));
    format.disabled = !options.length; setQualities();
  }
  media.addEventListener('change', setFormats); format.addEventListener('change', setQualities);
  filter.addEventListener('input', () => {historyLimit = 40; renderLists();});

  async function runAction(item: DownloadItem, action: string): Promise<void> {
    if (pendingActions.has(item.id)) return;
    pendingActions.add(item.id); renderLists();
    try {
      const result = await api<{message?: string}>(`/api/metube/${encodeURIComponent(item.id)}/${action}`, json('POST', {}));
      if (disposed) return;
      notify(result.message || 'Action completed.'); schedule(0);
    } catch (error) {
      if (!disposed) {message.textContent = error instanceof Error ? error.message : String(error); message.classList.add('error');}
    } finally {pendingActions.delete(item.id); if (!disposed) renderLists();}
  }
  function updateRow(item: DownloadItem, history: boolean): HTMLElement {
    let row = rows.get(item.id);
    if (!row) {
      const title = h('strong', {className: 'metube-item-title'}), badge = h('span', {className: 'metube-badge'});
      const detail = h('p', {className: 'metube-item-detail'}), progress = h('progress', {max: 100});
      const error = h('p', {className: 'metube-item-error'}), actions = h('div', {className: 'metube-actions'});
      const element = h('article', {className: 'metube-item', 'data-download-id': item.id},
        h('div', {className: 'metube-item-main'}, h('div', {className: 'metube-item-heading'}, title, badge), detail, progress, error), actions);
      row = {element, title, badge, detail, progress, error, actions, signature: '', actionSignature: ''}; rows.set(item.id, row);
    }
    const signature = JSON.stringify(item);
    if (row.signature !== signature) {
      row.signature = signature;
      row.title.textContent = item.title; row.title.title = item.title;
      row.badge.textContent = statusLabels[item.status] || item.status;
      row.badge.className = `metube-badge ${item.status === 'error' ? 'failed' : item.status === 'finished' ? 'completed' : ''}`;
      row.element.setAttribute('aria-label', `${item.title}, ${row.badge.textContent}`);
      const detail = [item.media === 'audio' ? 'Audio' : 'Video', item.format.toUpperCase()];
      if (item.quality && item.quality !== 'best') detail.push(item.quality === 'worst' ? 'Smallest' : `${item.quality}${item.media === 'audio' ? ' kbps' : 'p'}`);
      if (!history && item.percent !== null) detail.push(`${item.percent.toFixed(1)}%`);
      if (!history && item.speed !== null && item.speed > 0) detail.push(`${bytes(item.speed)}/s`);
      if (!history && item.eta !== null && item.eta > 0) detail.push(`${duration(item.eta)} remaining`);
      if (item.size !== null && item.size > 0) detail.push(bytes(item.size));
      if (history && item.timestamp) detail.push(new Date(item.timestamp * 1000).toLocaleString());
      if (history && item.filename) detail.push(item.filename);
      row.detail.textContent = detail.filter(Boolean).join(' · ');
      row.progress.hidden = history || ['pending', 'queued', 'scheduled'].includes(item.status);
      row.progress.setAttribute('aria-label', `Download progress for ${item.title}`);
      if (item.percent !== null) row.progress.value = item.percent; else row.progress.removeAttribute('value');
      row.error.textContent = item.error; row.error.hidden = !item.error;
    }
    const busy = pendingActions.has(item.id);
    const actionSignature = [item.can_retry, item.can_cancel, item.can_start, item.can_open, item.can_open_folder, busy].join();
    if (row.actionSignature !== actionSignature) {
      row.actionSignature = actionSignature;
      const actions: HTMLElement[] = [];
      for (const [allowed, label, action] of [[item.can_retry, 'Retry', 'retry'], [item.can_cancel, 'Cancel', 'cancel'],
        [item.can_start, 'Start download', 'start'], [item.can_open, 'Open file', 'open'], [item.can_open_folder, 'Open folder', 'open-folder']] as [boolean, string, string][]) {
        if (allowed) {const control = button(label, () => runAction(item, action)); control.disabled = busy; actions.push(control);}
      }
      row.actions.replaceChildren(...actions);
    }
    return row.element;
  }
  function reconcile(list: HTMLElement, items: DownloadItem[], history: boolean, emptyText: string): void {
    if (!items.length) {
      if (!list.querySelector('.empty-state')) list.replaceChildren(empty(emptyText));
      else list.querySelector('.empty-state')!.textContent = emptyText;
      return;
    }
    list.querySelector('.empty-state')?.remove();
    const wanted = new Set(items.map(item => item.id));
    for (const child of [...list.children]) if (!wanted.has((child as HTMLElement).dataset.downloadId || '')) child.remove();
    items.forEach((item, index) => {const node = updateRow(item, history); if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null);});
  }
  function renderLists(): void {
    queueCount.textContent = String(latest.queue.length + latest.pending.length); historyCount.textContent = String(latest.done.length);
    reconcile(queueList, [...latest.queue, ...latest.pending], false, online ? 'Your queue is clear. Add a URL above to start a download.' : 'Connect to MeTube to see your download queue.');
    const search = filter.value.trim().toLocaleLowerCase();
    const matching = latest.done.filter(item => !search || `${item.title} ${item.filename || ''} ${item.format} ${statusLabels[item.status] || ''}`.toLocaleLowerCase().includes(search));
    reconcile(historyList, matching.slice(0, historyLimit), true, search ? 'No downloads match your search.' : online ? 'Completed and failed downloads will appear here.' : 'Download history will appear when MeTube is connected.');
    showMore.hidden = matching.length <= historyLimit;
    const present = new Set([...latest.queue, ...latest.pending, ...latest.done].map(item => item.id));
    for (const key of rows.keys()) if (!present.has(key)) rows.delete(key);
  }
  function schedule(delay: number): void {
    if (timer) clearTimeout(timer);
    if (disposed || document.hidden) return;
    timer = setTimeout(() => {void poll();}, delay);
  }
  async function poll(): Promise<void> {
    if (disposed || document.hidden || polling) return;
    polling = true; request = new AbortController(); setState();
    try {
      // Format choices belong to the adapter; the frontend does not invent codec options.
      if (!formats) {formats = await api<Formats>('/api/metube/formats', {signal: request.signal}); if (disposed) return; setFormats();}
      const snapshot = await api<Snapshot>('/api/metube', {signal: request.signal});
      if (disposed) return;
      latest = snapshot; online = true; starting = false; renderLists();
    } catch (error) {
      if (disposed || request.signal.aborted) return;
      online = false;
      if (starting && Date.now() - startingAt > 600000) starting = false;
      serviceMessage.textContent = starting ? 'Starting Docker and MeTube. Waiting for the service to respond…' :
        error instanceof Error ? error.message : 'MeTube is unavailable. Start the service to connect.';
      renderLists();
    } finally {
      polling = false; request = undefined;
      if (!disposed) {setState(); schedule(starting ? 3000 : !online ? 15000 : latest.queue.length ? 2000 : 8000);}
    }
  }
  async function submitDownload(): Promise<void> {
    if (submitting || !form.reportValidity()) return;
    submitting = true; message.textContent = ''; message.classList.remove('error'); setState();
    const submittedUrl = url.value.trim();
    try {
      const result = await api<{message?: string}>('/api/metube/add', json('POST', {url: submittedUrl, media: media.value, format: format.value, quality: quality.value}));
      if (disposed) return;
      if (url.value.trim() === submittedUrl) url.value = '';
      message.textContent = result.message || 'Download added to your queue.'; schedule(0);
    } catch (error) {
      if (!disposed) {message.textContent = error instanceof Error ? error.message : String(error); message.classList.add('error');}
    } finally {submitting = false; if (!disposed) setState();}
  }
  form.addEventListener('submit', event => {event.preventDefault(); void submitDownload();});
  const onVisibility = (): void => {
    if (document.hidden) {if (timer) clearTimeout(timer); request?.abort();}
    else schedule(0);
  };
  document.addEventListener('visibilitychange', onVisibility);
  renderLists(); schedule(0);
  return () => {disposed = true; if (timer) clearTimeout(timer); request?.abort(); rows.clear(); document.removeEventListener('visibilitychange', onVisibility);};
}
