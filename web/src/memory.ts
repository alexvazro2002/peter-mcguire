import {api, button, dateLabel, downloadFile, empty, h, json, labelled, modal, notify} from './core.js';

interface Memory {
  id: string; content: string; category: string; source: string;
  temporary: boolean; pinned: boolean; created_at: string; updated_at: string;
}
interface MemoryResponse {memories: Memory[]; categories: Record<string, string>}

/** The dashboard always reads the assistant's real local store. No example entries. */
export async function renderMemory(host: HTMLElement): Promise<() => void> {
  let disposed = false, requestVersion = 0, searchTimer = 0;
  let categories: Record<string, string> = {}, memories: Memory[] = [];
  let dialog: ReturnType<typeof modal> | null = null;
  let controller: AbortController | null = null;
  const search = h('input', {type: 'search', placeholder: 'Search stored memories…', maxLength: 1000, 'aria-label': 'Search memories'});
  const category = h('select', {'aria-label': 'Filter memory category'}, h('option', {value: ''}, 'All categories'));
  const permanence = h('select', {'aria-label': 'Filter memory permanence'},
    h('option', {value: ''}, 'Permanent & temporary'), h('option', {value: 'permanent'}, 'Permanent only'), h('option', {value: 'temporary'}, 'Temporary only'));
  const pinned = h('input', {type: 'checkbox'});
  const count = h('p', {className: 'memory-count muted', role: 'status', 'aria-live': 'polite'});
  const list = h('div', {className: 'memory-grid', 'aria-label': 'Stored memories'});
  const error = h('div', {className: 'memory-error', role: 'alert', hidden: true});
  const clear = button('Clear temporary', () => confirmClear());
  const add = button('Add memory', () => edit(), 'primary');
  add.disabled = true;
  const exportButton = button('Export', async () => {
    exportButton.disabled = true;
    try {
      const result = await api<{content: string}>('/api/memories/export');
      downloadFile(`nexusdesk-memories-${new Date().toISOString().slice(0, 10)}.json`, result.content);
      notify('Personal memories exported.');
    } finally {exportButton.disabled = false;}
  });

  function setError(reason: unknown): void {
    error.replaceChildren(h('p', {}, reason instanceof Error ? reason.message : String(reason)), button('Retry', reload));
    error.hidden = false;
  }

  async function reload(): Promise<void> {
    const version = ++requestVersion;
    controller?.abort(); controller = new AbortController();
    list.setAttribute('aria-busy', 'true'); error.hidden = true;
    try {
      const params = new URLSearchParams({q: search.value.trim(), category: category.value});
      const data = await api<MemoryResponse>(`/api/memories?${params}`, {signal: controller.signal});
      if (disposed || version !== requestVersion) return;
      memories = data.memories; categories = data.categories;
      add.disabled = false;
      const selected = category.value;
      category.replaceChildren(h('option', {value: ''}, 'All categories'),
        ...Object.entries(categories).map(([value, text]) => h('option', {value}, text)));
      category.value = selected;
      draw();
    } catch (reason) {
      if (disposed || version !== requestVersion || reason instanceof DOMException && reason.name === 'AbortError') return;
      count.textContent = 'Memories could not be loaded.';
      list.replaceChildren(); setError(reason);
    } finally {
      if (version === requestVersion) list.removeAttribute('aria-busy');
    }
  }

  async function patch(memory: Memory, values: Partial<Memory>, control: HTMLButtonElement): Promise<void> {
    control.disabled = true;
    try {await api(`/api/memories/${memory.id}`, json('PATCH', values)); await reload();}
    finally {control.disabled = false;}
  }

  function draw(): void {
    const filtered = memories.filter(memory => (!pinned.checked || memory.pinned) &&
      (!permanence.value || memory.temporary === (permanence.value === 'temporary')));
    count.textContent = `${filtered.length} ${filtered.length === 1 ? 'memory' : 'memories'}${search.value || category.value || pinned.checked || permanence.value ? ' matching your filters' : ' stored locally'}`;
    list.replaceChildren();
    if (!filtered.length) {
      list.append(empty(search.value || category.value || pinned.checked || permanence.value
        ? 'No memories match these filters.' : 'No personal memories have been stored yet. Add a fact yourself, or explicitly ask the local assistant to remember one.'));
      return;
    }
    for (const memory of filtered) {
      const pin = button(memory.pinned ? 'Unpin' : 'Pin', () => patch(memory, {pinned: !memory.pinned}, pin));
      pin.setAttribute('aria-pressed', String(memory.pinned));
      pin.setAttribute('aria-label', `${memory.pinned ? 'Unpin' : 'Pin'} memory: ${memory.content.slice(0, 70)}`);
      const permanent = button(memory.temporary ? 'Make permanent' : 'Make temporary',
        () => patch(memory, {temporary: !memory.temporary}, permanent));
      list.append(h('article', {className: 'memory-card'},
        h('div', {className: 'memory-meta'}, h('span', {className: 'badge'}, categories[memory.category] || memory.category),
          h('span', {className: `badge ${memory.temporary ? 'temporary' : ''}`}, memory.temporary ? 'Temporary' : 'Permanent'),
          memory.pinned && h('span', {className: 'badge'}, 'Pinned')),
        h('p', {className: 'memory-content'}, memory.content),
        h('div', {className: 'memory-details muted'}, h('span', {}, memory.source ? `Source: ${memory.source}` : 'Source not recorded'),
          h('time', {dateTime: memory.updated_at, title: `Created ${dateLabel(memory.created_at)}`}, `Updated ${dateLabel(memory.updated_at)}`)),
        h('div', {className: 'memory-actions'}, button('Edit', () => edit(memory)), pin, permanent,
          button('Delete', () => confirmDelete(memory), 'danger'))));
    }
  }

  function confirmAction(title: string, description: string, text: string, action: () => Promise<unknown>): void {
    dialog?.close();
    let ownDialog: ReturnType<typeof modal>;
    const problem = h('p', {role: 'alert', className: 'error', hidden: true});
    const body = h('div', {}, h('p', {}, description), problem);
    const cancel = button('Cancel', () => ownDialog.close());
    const confirm = button(text, async () => {
      confirm.disabled = true; cancel.disabled = true; problem.hidden = true;
      try {await action(); ownDialog.close(); await reload();}
      catch (reason) {problem.textContent = reason instanceof Error ? reason.message : String(reason); problem.hidden = false;}
      finally {confirm.disabled = false; cancel.disabled = false;}
    }, 'danger');
    dialog = ownDialog = modal(title, body, [cancel, confirm]); cancel.focus();
  }

  function confirmDelete(memory: Memory): void {
    confirmAction('Delete this memory?', `This removes the stored memory “${memory.content.slice(0, 220)}${memory.content.length > 220 ? '…' : ''}”. This cannot be undone from the dashboard.`, 'Delete memory',
      () => api(`/api/memories/${memory.id}`, json('DELETE', {confirmed: true})));
  }

  function confirmClear(): void {
    confirmAction('Clear all temporary memories?', 'Every temporary memory will be deleted, including pinned temporary memories and entries hidden by filters. Permanent memories will be kept. This cannot be undone from the dashboard.', 'Clear temporary memories',
      () => api('/api/memories/clear-temporary', json('POST', {confirmed: true})));
  }

  function edit(memory?: Memory): void {
    dialog?.close();
    let ownDialog: ReturnType<typeof modal>;
    const content = h('textarea', {rows: 5, required: true, maxLength: 4000, value: memory?.content || '', placeholder: 'A fact you want the assistant to remember'});
    const choice = h('select', {}, ...Object.entries(categories).map(([value, title]) => h('option', {value}, title)));
    choice.value = memory?.category || 'personal';
    const source = h('input', {type: 'text', maxLength: 300, value: memory?.source ?? 'Manual entry'});
    const temporary = h('input', {type: 'checkbox', checked: memory?.temporary || false});
    const important = h('input', {type: 'checkbox', checked: memory?.pinned || false});
    const problem = h('p', {className: 'error', role: 'alert', hidden: true});
    const form = h('form', {className: 'memory-form'}, labelled('Stored information', content),
      labelled('Category', choice), labelled('Source / origin', source),
      h('div', {className: 'memory-form-options'}, h('label', {}, temporary, ' Temporary memory'), h('label', {}, important, ' Pin important memory')),
      h('p', {className: 'muted'}, 'Stored on this device. The local assistant can read these facts and update them when you ask. Do not enter credentials or private system prompts.'), problem);
    const save = h('button', {type: 'submit', className: 'primary'}, memory ? 'Save changes' : 'Add memory');
    const cancel = button('Cancel', () => ownDialog.close());
    form.append(h('div', {className: 'dialog-actions'}, cancel, save));
    form.addEventListener('submit', event => {
      event.preventDefault(); if (save.disabled || !form.reportValidity()) return;
      save.disabled = true; problem.hidden = true;
      const values = {content: content.value, category: choice.value, source: source.value, temporary: temporary.checked, pinned: important.checked};
      void api(memory ? `/api/memories/${memory.id}` : '/api/memories', json(memory ? 'PATCH' : 'POST', values))
        .then(async () => {ownDialog.close(); await reload(); notify(memory ? 'Memory updated.' : 'Memory added.');})
        .catch((reason: unknown) => {problem.textContent = reason instanceof Error ? reason.message : String(reason); problem.hidden = false;})
        .finally(() => {save.disabled = false;});
    });
    dialog = ownDialog = modal(memory ? 'Edit personal memory' : 'Add personal memory', form); content.focus();
  }

  search.addEventListener('input', () => {window.clearTimeout(searchTimer); searchTimer = window.setTimeout(() => {void reload();}, 150);});
  category.addEventListener('change', () => {void reload();});
  permanence.addEventListener('change', draw); pinned.addEventListener('change', draw);
  host.replaceChildren(h('section', {className: 'memory-page'},
    h('header', {className: 'page-header'}, h('div', {}, h('h1', {}, 'Personal Memory'), h('p', {className: 'muted'}, 'The facts and preferences you have chosen to share with your local assistant.')),
      h('div', {className: 'toolbar'}, exportButton, clear, add)),
    h('div', {className: 'memory-filters'}, search, category, permanence, h('label', {className: 'memory-pinned-filter'}, pinned, ' Pinned only')),
    count, error, list));
  count.textContent = 'Loading personal memories…';
  await reload();
  return () => {disposed = true; ++requestVersion; controller?.abort(); window.clearTimeout(searchTimer); dialog?.close();};
}
