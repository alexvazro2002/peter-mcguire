export interface NoteMeta {id: string; title: string; folder: string; project: string; tags: string[]; last_opened?: number; link_count?: number}
export type Child = Node | string | number | null | undefined | false;
let csrf = '';
export function setCsrf(value: string): void {csrf = value;}
export async function api<T = Record<string, unknown>>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (options.method && options.method !== 'GET') headers.set('X-Nexus-CSRF', csrf);
  const response = await fetch(path, {...options, headers, credentials: 'same-origin'});
  if (!response.ok) {
    const error = await response.json().catch(() => ({detail: `Request failed (${response.status})`})) as {detail?: unknown};
    throw new Error(typeof error.detail === 'string' ? error.detail : `Request failed (${response.status})`);
  }
  return await response.json() as T;
}
export function json(method: string, body: unknown): RequestInit {return {method, body: JSON.stringify(body)};}
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, unknown> | null = null, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    else if (key === 'className') node.className = String(value);
    else if (key === 'textContent') node.textContent = String(value);
    else if (key in node && !key.startsWith('aria')) (node as unknown as Record<string, unknown>)[key] = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
export function button(text: string, action: () => unknown, className = ''): HTMLButtonElement {
  return h('button', {type: 'button', className, onclick: () => {try {const result = action(); if (result instanceof Promise) void result.catch(showError);} catch (error) {showError(error);}}}, text);
}
export function showError(error: unknown): void {notify(error instanceof Error ? error.message : String(error), true);}
export function notify(text: string, error = false): void {
  const area = document.getElementById('notifications');
  if (!area) return;
  const item = h('div', {className: `toast ${error ? 'error' : ''}`, role: error ? 'alert' : 'status'}, text);
  area.append(item); setTimeout(() => item.remove(), error ? 10000 : 4000);
}
export function navigate(route: string, id?: string, extra?: Record<string, unknown>): void {
  window.dispatchEvent(new CustomEvent('nexus:navigate', {detail: {route, id, extra}}));
}
export function modal(title: string, body: HTMLElement, actions: HTMLElement[] = []): {element: HTMLDialogElement; close: () => void} {
  const element = h('dialog', {className: 'dialog', 'aria-label': title});
  const close = (): void => {element.close(); element.remove();};
  element.append(h('header', {}, h('h2', {}, title), button('×', close, 'icon-button close-dialog')), body);
  if (actions.length) element.append(h('footer', {className: 'dialog-actions'}, ...actions));
  element.addEventListener('close', () => element.remove());
  document.body.append(element); element.showModal();
  return {element, close};
}
export function contextMenu(event: MouseEvent, actions: {title: string; action: () => unknown; disabled?: boolean}[]): void {
  document.querySelectorAll('.context-menu').forEach(e => e.remove());
  const menu = h('div', {className: 'context-menu', role: 'menu'});
  const close = (): void => menu.remove();
  actions.forEach(item => {const b = button(item.title, () => {close(); return item.action();}); b.setAttribute('role', 'menuitem'); b.disabled = !!item.disabled; menu.append(b);});
  document.body.append(menu);
  menu.style.left = `${Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 12)}px`;
  menu.style.top = `${Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 12)}px`;
  const outside = (e: PointerEvent): void => {if (!menu.contains(e.target as Node)) close();};
  setTimeout(() => document.addEventListener('pointerdown', outside, {once: true}), 0);
  menu.addEventListener('keydown', e => {
    const items = [...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {e.preventDefault(); items[(index + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();}
  });
  menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
}
export function labelled(text: string, control: HTMLElement): HTMLLabelElement {return h('label', {className: 'field'}, h('span', {}, text), control);}
export function empty(text: string): HTMLElement {return h('p', {className: 'empty-state'}, text);}
export function dateLabel(value?: string | null): string {return value ? new Date(value).toLocaleString() : 'Never';}
export function downloadFile(name: string, content: string, type = 'application/json'): void {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const anchor = h('a', {href: url, download: name}); anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
