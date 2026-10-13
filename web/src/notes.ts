import {api, button, downloadFile, empty, h, json, labelled, modal, navigate, notify, showError, type NoteMeta} from './core';

interface LinkOccurrence {start:number; end:number; label_start:number; label_end:number; words:string; target:string; target_id:string|null; target_name:string|null; broken:boolean; kind:'id'|'wiki'; excerpt?:string}
interface Backlink extends LinkOccurrence {source:string; source_id:string; source_metadata:NoteMeta}
interface NoteDocument extends NoteMeta {content:string; revision:string; links:LinkOccurrence[]; backlinks:Backlink[]}
interface Segment {start:number; end:number; visibleStart:number; visibleEnd:number; labelStart:number; labelEnd:number; anchor?:HTMLAnchorElement; map?:number[]}
interface Snapshot {raw:string; segments:Segment[]}
interface Selected {start:number; end:number; visibleStart:number; visibleEnd:number; link?:Segment; raw:string}

export function codePointToUtf16(text:string, offset:number):number {return Array.from(text).slice(0, offset).join('').length;}
export function utf16ToCodePoint(text:string, offset:number):number {return Array.from(text.slice(0, offset)).length;}
function escapeLabel(text:string):string {return text.replace(/\\/g, '\\\\').replace(/\[/g, '\\[').replace(/\]/g, '\\]');}
function metadata(note:NoteMeta):string {return [note.project, note.folder, ...(note.tags || []).map(t => `#${t}`)].filter(Boolean).join(' · ');}
function draftKey(id:string):string {return `nexus:note-draft:${id}`;}

/** Only link labels are rendered. Every other character, including Markdown, stays literal. */
export function renderDocumentSurface(surface:HTMLElement, content:string, links:LinkOccurrence[]):void {
  const nodes:Node[] = []; let cursor = 0;
  for (const link of links) {
    const start = codePointToUtf16(content, link.start), end = codePointToUtf16(content, link.end);
    const labelStart = codePointToUtf16(content, link.label_start), labelEnd = codePointToUtf16(content, link.label_end);
    if (start < cursor || end > content.length) continue;
    nodes.push(document.createTextNode(content.slice(cursor, start)));
    const anchor = h('a', {className:`note-text-link${link.broken ? ' broken-link' : ''}`, href:link.target_id ? `#notes/${encodeURIComponent(link.target_id)}` : '#', title:link.broken ? `Broken link: ${link.target}` : `Open ${link.target_name || link.target}`, 'aria-label':`${link.words} — ${link.broken ? 'broken link' : `open ${link.target_name || link.target}`}`}, link.words);
    anchor.dataset.prefix = content.slice(start, labelStart); anchor.dataset.suffix = content.slice(labelEnd, end);
    anchor.dataset.original = content.slice(labelStart, labelEnd); anchor.dataset.words = link.words; anchor.dataset.kind = link.kind;
    anchor.dataset.target = link.target_id || ''; anchor.dataset.broken = String(link.broken);
    nodes.push(anchor); cursor = end;
  }
  nodes.push(document.createTextNode(content.slice(cursor))); surface.replaceChildren(...nodes);
}

/** Source offsets remain Unicode-safe even when a label contains escaped brackets or emoji. */
export function snapshotSurface(surface:HTMLElement):Snapshot {
  let raw = '', visible = 0; const segments:Segment[] = [];
  function visit(node:Node):void {
    if (node instanceof HTMLAnchorElement && node.classList.contains('note-text-link')) {
      const words = node.textContent || '', original = node.dataset.original || '';
      const label = words === node.dataset.words ? original : node.dataset.kind === 'id' ? escapeLabel(words) : words;
      const prefix = node.dataset.prefix || '', suffix = node.dataset.suffix || '', map = [0];
      // Map each rendered UTF-16 boundary back into its lossless source label.
      for (let i=0; i<label.length;) {
        if (node.dataset.kind === 'id' && label[i] === '\\' && /[\\\[\]]/.test(label[i+1] || '')) i++;
        i++; map.push(i);
      }
      const start = raw.length; raw += prefix + label + suffix;
      segments.push({start, end:raw.length, visibleStart:visible, visibleEnd:visible+words.length, labelStart:start+prefix.length, labelEnd:start+prefix.length+label.length, anchor:node, map});
      visible += words.length; return;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || '', start=raw.length; raw += text;
      segments.push({start, end:raw.length, visibleStart:visible, visibleEnd:visible+text.length, labelStart:start, labelEnd:raw.length}); visible += text.length; return;
    }
    // Normal editing inserts literal newlines. Accommodate browser-generated breaks as well.
    if (node instanceof HTMLBRElement) {raw+='\n'; visible++; return;}
    for (const child of node.childNodes) visit(child);
  }
  for (const child of surface.childNodes) visit(child);
  return {raw, segments};
}

function visibleOffset(surface:HTMLElement, node:Node, offset:number):number {
  const range=document.createRange(); range.selectNodeContents(surface); range.setEnd(node,offset); return range.toString().length;
}
function sourceOffset(snapshot:Snapshot, visible:number, end=false):number {
  const candidates=snapshot.segments.filter(s => visible>=s.visibleStart && visible<=s.visibleEnd);
  const segment=(end ? candidates.find(s=>visible>s.visibleStart) : [...candidates].reverse().find(s=>visible<s.visibleEnd)) || candidates[0];
  if (!segment) return snapshot.raw.length;
  const local=Math.max(0, visible-segment.visibleStart);
  return segment.labelStart+(segment.map ? (segment.map[local] ?? segment.labelEnd-segment.labelStart) : local);
}
function readSelection(surface:HTMLElement, snapshot:Snapshot):Selected|null {
  const selection=window.getSelection(); if (!selection?.rangeCount || selection.isCollapsed) return null;
  const range=selection.getRangeAt(0); if (!surface.contains(range.startContainer) || !surface.contains(range.endContainer)) return null;
  const visibleStart=visibleOffset(surface,range.startContainer,range.startOffset), visibleEnd=visibleOffset(surface,range.endContainer,range.endOffset);
  const start=sourceOffset(snapshot,visibleStart), end=sourceOffset(snapshot,visibleEnd,true);
  const link=snapshot.segments.find(s=>s.anchor && visibleStart>=s.visibleStart && visibleEnd<=s.visibleEnd && visibleEnd>visibleStart);
  return {start,end,visibleStart,visibleEnd,link,raw:snapshot.raw};
}
function restoreSelection(surface:HTMLElement,start:number,end=start):void {
  const walker=document.createTreeWalker(surface,NodeFilter.SHOW_TEXT); let node:Node|null, cursor=0;
  let first:{node:Node;offset:number}|undefined, last:{node:Node;offset:number}|undefined;
  while ((node=walker.nextNode())) {
    const length=node.textContent?.length || 0;
    if (!first && start<=cursor+length) first={node,offset:Math.max(0,start-cursor)};
    if (end<=cursor+length) {last={node,offset:Math.max(0,end-cursor)};break;} cursor+=length;
  }
  if (!first) return; last ||= first;
  const range=document.createRange(); range.setStart(first.node,first.offset); range.setEnd(last.node,last.offset);
  const selection=window.getSelection(); selection?.removeAllRanges(); selection?.addRange(range);
}
function rawToVisible(snapshot:Snapshot, raw:number):number {
  for (const segment of snapshot.segments) if (raw>=segment.start && raw<=segment.end) {
    if (!segment.anchor) return segment.visibleStart+raw-segment.start;
    if (raw<=segment.labelStart) return segment.visibleStart;
    if (raw>=segment.labelEnd) return segment.visibleEnd;
    const local=raw-segment.labelStart; return segment.visibleStart+(segment.map?.findIndex(x=>x>=local) ?? local);
  }
  return snapshot.segments.at(-1)?.visibleEnd || 0;
}

export async function renderNotes(host:HTMLElement,id?:string,extra:Record<string,unknown>={}):Promise<()=>void> {
  let disposed=false, doc:NoteDocument|null=null, editing=true, dirty=false, conflict=false, saving:Promise<boolean>|null=null;
  let saveTimer:number|undefined, searchTimer:number|undefined, queryVersion=0, interactions=0;
  let closeInteraction:(()=>void)|null=null;
  const list=h('div',{className:'note-list',role:'list','aria-label':'Notes'}), search=h('input',{type:'search',placeholder:'Search notes…','aria-label':'Search notes'});
  const title=h('h1',{},'Notes'), state=h('span',{className:'note-save-state',role:'status','aria-live':'polite'});
  const surface=h('div',{className:'note-surface',contentEditable:'true',role:'textbox','aria-multiline':'true','aria-label':'Note editor',spellcheck:true,tabIndex:0});
  const backlinks=h('section',{className:'note-backlinks','aria-label':'Backlinks'}), recovery=h('div',{className:'note-recovery'});
  const toolbar=h('div',{className:'note-actions'}), noteArea=h('section',{className:'note-detail'},h('header',{className:'note-heading'},title,toolbar),recovery,surface,backlinks);
  const editorToggle=button('View',()=>{editing=!editing; surface.contentEditable=String(editing); surface.setAttribute('role',editing?'textbox':'document');surface.setAttribute('aria-label',editing?'Note editor':'Note viewer');editorToggle.textContent=editing?'View':'Edit';});
  toolbar.append(state,editorToggle,button('Save',()=>save()),button('Graph',()=>openGraph()),button('Rename',()=>rename()),button('Delete',()=>remove(),'danger subtle'));
  host.replaceChildren(h('div',{className:'notes-workspace'},h('aside',{className:'notes-sidebar'},h('div',{className:'note-sidebar-heading'},h('strong',{},'Notes'),button('+ New',()=>createNote())),search,list),noteArea));

  function rememberDraft(raw:string):void {
    if (!doc) return;
    try {localStorage.setItem(draftKey(doc.id),JSON.stringify({content:raw,revision:doc.revision,updated:new Date().toISOString()}));} catch {state.textContent='Unsaved — local draft storage is unavailable';}
  }
  function forgetDraft(noteId:string, content:string):void {
    try {const stored=JSON.parse(localStorage.getItem(draftKey(noteId)) || 'null') as {content:string}|null;if (stored?.content===content) localStorage.removeItem(draftKey(noteId));} catch { /* Storage may be disabled. */ }
  }
  function showConflict(error:unknown):void {
    conflict=true; state.textContent='Save failed — your draft is preserved'; recovery.replaceChildren(h('p',{},error instanceof Error?error.message:'Could not save the note.'),button('Retry save',()=>{conflict=false;recovery.replaceChildren();return save();}),button('Download draft',()=>downloadFile(`${doc?.title || 'note'}-draft.md`,snapshotSurface(surface).raw,'text/markdown')),button('Save as a new note',()=>createNote(snapshotSurface(surface).raw)),button('Compare with disk',async()=>{
      if (!doc) return;const current=await api<NoteDocument>(`/api/notes/${encodeURIComponent(doc.id)}`);
      const content=h('div',{},h('p',{},'Your draft remains in the editor. The saved version on disk is shown below.'),h('pre',{className:'note-disk-version'},current.content));
      modal('Saved version',content);
    }));
  }
  async function save():Promise<boolean> {
    window.clearTimeout(saveTimer); if (saving) {await saving;return dirty && !conflict ? save() : !conflict;}
    if (!doc || !dirty) return true;
    if (conflict) return false;
    const current=doc, raw=snapshotSurface(surface).raw;rememberDraft(raw);state.textContent='Saving…';
    saving=(async()=>{
      try {
        const saved=await api<NoteDocument>(`/api/notes/${encodeURIComponent(current.id)}`,json('PATCH',{revision:current.revision,content:raw}));
        if (doc?.id!==current.id) return true;
        doc=saved;dirty=snapshotSurface(surface).raw!==raw;forgetDraft(current.id,raw);
        if (dirty) rememberDraft(snapshotSurface(surface).raw);
        if (!disposed) {state.textContent=dirty?'Unsaved changes':'Saved';renderBacklinks();}
        if (!dirty && !interactions && !disposed) {
          const selection=window.getSelection(), range=selection?.rangeCount?selection.getRangeAt(0):null;
          const inside=range && surface.contains(range.startContainer) && surface.contains(range.endContainer);
          const start=inside?visibleOffset(surface,range.startContainer,range.startOffset):0,end=inside?visibleOffset(surface,range.endContainer,range.endOffset):0;
          renderDocumentSurface(surface,saved.content,saved.links);if(inside)restoreSelection(surface,start,end);
        }
        return true;
      } catch(error) {if(!disposed)showConflict(error);return false;}
      finally {saving=null;}
    })();
    return saving;
  }
  function changed():void {
    if(!doc)return;dirty=snapshotSurface(surface).raw!==doc.content;state.textContent=dirty?'Unsaved changes':'Saved';
    if(dirty)rememberDraft(snapshotSurface(surface).raw);window.clearTimeout(saveTimer);
    if(dirty&&!conflict)saveTimer=window.setTimeout(()=>{if(!interactions)void save();},700);
  }
  function insertText(text:string):void {
    if(!editing)return;const selection=window.getSelection();if(!selection?.rangeCount)return;
    const range=selection.getRangeAt(0);if(!surface.contains(range.commonAncestorContainer))return;
    // insertText participates in the browser's native undo stack where supported.
    if(document.execCommand('insertText',false,text)){changed();return;}
    range.deleteContents();const node=document.createTextNode(text);range.insertNode(node);range.setStartAfter(node);range.collapse(true);selection.removeAllRanges();selection.addRange(range);changed();
  }
  surface.addEventListener('beforeinput',(event:InputEvent)=>{if(event.inputType==='insertParagraph'||event.inputType==='insertLineBreak'){event.preventDefault();insertText('\n');}});
  surface.addEventListener('paste',event=>{if(editing){event.preventDefault();insertText(event.clipboardData?.getData('text/plain')||'');}});
  surface.addEventListener('drop',event=>{event.preventDefault();if(editing)insertText(event.dataTransfer?.getData('text/plain')||'');});
  surface.addEventListener('input',changed);
  surface.addEventListener('click',event=>{
    const anchor=(event.target as Element).closest<HTMLAnchorElement>('.note-text-link');if(!anchor)return;event.preventDefault();
    if(window.getSelection()?.isCollapsed!==false){if(anchor.dataset.broken==='true')notify('This destination is missing. Select its text and right-click to change or remove the link.',true);else void openNote(anchor.dataset.target || '');}
  });
  surface.addEventListener('contextmenu',event=>{
    event.preventDefault();if(!doc)return;
    const selected=readSelection(surface,snapshotSurface(surface));
    if(!selected){void openGraph();return;}
    closeInteraction?.();interactions++;window.clearTimeout(saveTimer);
    const menu=h('div',{className:'context-menu note-link-menu',role:'menu','aria-label':selected.link?'Note link actions':'Selected text actions'});
    let closed=false;
    const close=(restore=true):void=>{if(closed)return;closed=true;menu.remove();document.removeEventListener('pointerdown',outside);interactions--;closeInteraction=null;if(restore){surface.focus();restoreSelection(surface,selected.visibleStart,selected.visibleEnd);}if(dirty&&!conflict)saveTimer=window.setTimeout(()=>void save(),700);};
    const outside=(e:PointerEvent):void=>{if(!menu.contains(e.target as Node))close();};
    const add=(text:string,action:()=>unknown,disabled=false):void=>{const control=button(text,()=>{close(false);return action();});control.setAttribute('role','menuitem');control.disabled=disabled;menu.append(control);};
    if(selected.link){const anchor=selected.link.anchor!,target=anchor.dataset.target || '',broken=anchor.dataset.broken==='true';
      add('Open destination',()=>openNote(target),broken||!target);add('Change destination',()=>chooseDestination(selected));add('Remove link',()=>applyLink(selected,'remove'));add('Open destination in graph',()=>openGraph(target),broken||!target);
    }else add('Link',()=>chooseDestination(selected));
    document.body.append(menu);menu.style.left=`${Math.max(8,Math.min(event.clientX,window.innerWidth-menu.offsetWidth-8))}px`;menu.style.top=`${Math.max(8,Math.min(event.clientY,window.innerHeight-menu.offsetHeight-8))}px`;
    menu.addEventListener('keydown',e=>{const controls=[...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')],at=controls.indexOf(document.activeElement as HTMLButtonElement);if(e.key==='Escape'){e.preventDefault();close();}if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();controls[(at+(e.key==='ArrowDown'?1:controls.length-1))%controls.length]?.focus();}});
    document.addEventListener('pointerdown',outside);closeInteraction=()=>close();menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  });

  async function applyLink(selected:Selected, action:'link'|'remove', destination?:string):Promise<void> {
    if(!doc)return;
    if(snapshotSurface(surface).raw!==selected.raw){notify('The text changed. Select it again before linking.',true);return;}
    if(!(await save()))return;
    const result=await api<NoteDocument>(`/api/notes/${encodeURIComponent(doc.id)}/links`,json('POST',{revision:doc.revision,start:utf16ToCodePoint(selected.raw,selected.start),end:utf16ToCodePoint(selected.raw,selected.end),action,destination_id:destination}));
    doc=result;dirty=false;conflict=false;recovery.replaceChildren();renderDocumentSurface(surface,result.content,result.links);renderBacklinks();state.textContent='Saved';surface.focus();restoreSelection(surface,selected.visibleStart,selected.visibleEnd);
  }
  function chooseDestination(selected:Selected):void {
    if(!doc)return;interactions++;window.clearTimeout(saveTimer);let version=0,active=0,destinations:NoteMeta[]=[],closed=false,searchTimeout:number|undefined;
    const query=h('input',{type:'search',placeholder:'Find a destination note…','aria-label':'Search destination notes',role:'combobox','aria-autocomplete':'list','aria-controls':'note-destinations','aria-expanded':'true'});
    const choices=h('div',{id:'note-destinations',className:'note-destinations',role:'listbox','aria-label':'Destination notes'}),hint=h('p',{className:'muted'},'Recent and frequently linked notes appear first. ↑ ↓ to choose · Enter to link · Esc to cancel');
    const dialog=modal('Link to a note',h('div',{},h('p',{className:'note-selected-phrase'},'Selected text: ',selected.raw.slice(selected.start,selected.end)),query,hint,choices));
    const finish=():void=>{if(closed)return;closed=true;window.clearTimeout(searchTimeout);interactions--;closeInteraction=null;surface.focus();restoreSelection(surface,selected.visibleStart,selected.visibleEnd);};
    dialog.element.addEventListener('close',finish);closeInteraction=()=>{dialog.close();finish();};
    const activate=():void=>{[...choices.children].forEach((item,i)=>{item.setAttribute('aria-selected',String(i===active));item.classList.toggle('selected',i===active);});query.setAttribute('aria-activedescendant',`note-destination-${active}`);choices.children[active]?.scrollIntoView({block:'nearest'});};
    const choose=async(index:number):Promise<void>=>{const target=destinations[index];if(!target)return;query.disabled=true;for(const child of choices.querySelectorAll<HTMLButtonElement>('button'))child.disabled=true;try{await applyLink(selected,'link',target.id);dialog.close();finish();}catch(error){showError(error);query.disabled=false;for(const child of choices.querySelectorAll<HTMLButtonElement>('button'))child.disabled=false;query.focus();}};
    const load=async():Promise<void>=>{const request=++version;try{const response=await api<{notes:NoteMeta[]}>(`/api/notes/selector?q=${encodeURIComponent(query.value)}&exclude=${encodeURIComponent(doc!.id)}`);if(closed||request!==version)return;destinations=response.notes;active=0;choices.replaceChildren(...destinations.map((note,index)=>{const item=button('',()=>choose(index),'note-destination');item.id=`note-destination-${index}`;item.setAttribute('role','option');item.append(h('strong',{},note.title),h('small',{},metadata(note)));item.addEventListener('pointermove',()=>{active=index;activate();});return item;}));if(!destinations.length)choices.append(empty('No matching notes. Try a different title, project or tag.'));activate();}catch(error){if(!closed)choices.replaceChildren(empty(error instanceof Error?error.message:'Could not load notes.'));}};
    query.addEventListener('input',()=>{window.clearTimeout(searchTimeout);searchTimeout=window.setTimeout(()=>void load(),90);});
    query.addEventListener('keydown',e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=(active+(e.key==='ArrowDown'?1:destinations.length-1))%Math.max(1,destinations.length);activate();}else if(e.key==='Enter'){e.preventDefault();void choose(active);}});
    void load();query.focus();
  }
  function renderBacklinks():void {
    if(!doc)return;backlinks.replaceChildren(h('h2',{},`Backlinks · ${doc.backlinks.length}`));
    if(!doc.backlinks.length){backlinks.append(h('p',{className:'muted'},'No notes link here yet.'));return;}
    for(const link of doc.backlinks){const item=button('',()=>openNote(link.source_id,{start:link.start,end:link.end}),'backlink');item.append(h('strong',{},link.source),h('span',{},link.excerpt || link.words),h('small',{},metadata(link.source_metadata)));backlinks.append(item);}
  }
  async function openNote(noteId:string,range?:Record<string,unknown>):Promise<void>{if(noteId&&await save())navigate('notes',noteId,range);}
  async function openGraph(noteId=doc?.id):Promise<void>{if(noteId&&await save())navigate('graph',noteId);}
  async function loadList():Promise<NoteMeta[]> {
    const request=++queryVersion;const response=await api<{notes:NoteMeta[]}>(`/api/notes?q=${encodeURIComponent(search.value)}`);
    if(disposed||request!==queryVersion)return response.notes;
    list.replaceChildren(...response.notes.map(note=>{const row=button('',()=>openNote(note.id),`note-list-item${note.id===doc?.id?' active':''}`);row.setAttribute('role','listitem');row.setAttribute('aria-current',note.id===doc?.id?'page':'false');row.append(h('strong',{},note.title),h('small',{},metadata(note)));return row;}));
    if(!response.notes.length)list.append(empty(search.value?'No matching notes.':'No notes yet. Create your first note.'));return response.notes;
  }
  function createNote(content?:string):void {
    const input=h('input',{required:true,maxLength:180,placeholder:'Note title','aria-label':'New note title'}),form=h('form',{},labelled('Title',input));
    const dialog=modal('New note',form);form.append(button('Create note',async()=>{if(!input.value.trim()){input.reportValidity();return;}if(!content&&!(await save()))return;const result=await api<NoteDocument>('/api/notes',json('POST',{title:input.value,content}));dialog.close();navigate('notes',result.id);},'primary'));
    form.addEventListener('submit',event=>{event.preventDefault();form.querySelector<HTMLButtonElement>('button')?.click();});input.focus();
  }
  function rename():void {
    if(!doc)return;const input=h('input',{value:doc.title,required:true,maxLength:180,'aria-label':'Note title'}),body=h('form',{},labelled('Title',input));const dialog=modal('Rename note',body);
    body.append(button('Rename',async()=>{if(!input.value.trim()){input.reportValidity();return;}if(!(await save())||!doc)return;doc=await api<NoteDocument>(`/api/notes/${encodeURIComponent(doc.id)}`,json('PATCH',{revision:doc.revision,title:input.value}));title.textContent=doc.title;dialog.close();await loadList();},'primary'));body.addEventListener('submit',event=>{event.preventDefault();body.querySelector<HTMLButtonElement>('button')?.click();});input.focus();input.select();
  }
  function remove():void {
    if(!doc)return;const titleToDelete=doc.title,idToDelete=doc.id;const dialog=modal('Delete note',h('div',{},h('p',{},`Move “${titleToDelete}” to the vault’s .trash folder? Other notes will keep their linked text and show a broken link.`)));
    dialog.element.append(h('footer',{className:'dialog-actions'},button('Cancel',()=>dialog.close()),button('Move to trash',async()=>{if(!(await save()))return;await api(`/api/notes/${encodeURIComponent(idToDelete)}`,json('DELETE',{confirmed:true}));dialog.close();navigate('notes');},'danger')));
  }
  search.addEventListener('input',()=>{window.clearTimeout(searchTimer);searchTimer=window.setTimeout(()=>void loadList().catch(showError),100);});
  const keyboard=(event:KeyboardEvent):void=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();void save();}};
  const beforeUnload=(event:BeforeUnloadEvent):void=>{if(dirty){rememberDraft(snapshotSurface(surface).raw);event.preventDefault();event.returnValue='';}};
  window.addEventListener('keydown',keyboard);window.addEventListener('beforeunload',beforeUnload);
  try {
    state.textContent='Loading…';const notes=await loadList();if(disposed)return()=>{};
    if(!id)id=notes[0]?.id;
    if(id){doc=await api<NoteDocument>(`/api/notes/${encodeURIComponent(id)}`);title.textContent=doc.title;renderDocumentSurface(surface,doc.content,doc.links);renderBacklinks();state.textContent='Saved';await loadList();
      try {const draft=JSON.parse(localStorage.getItem(draftKey(doc.id))||'null') as {content:string;revision:string;updated:string}|null;
        if(draft&&typeof draft.content==='string'&&draft.content!==doc.content){recovery.append(h('p',{},'A previous unsaved draft is available.'),button('Restore draft',()=>{if(!doc)return;renderDocumentSurface(surface,draft.content,[]);dirty=true;rememberDraft(draft.content);recovery.replaceChildren();state.textContent='Restored draft — Save to keep';}),button('Download draft',()=>downloadFile(`${doc?.title}-draft.md`,draft.content,'text/markdown')),button('Discard saved draft',()=>{const confirm=modal('Discard draft',h('p',{},'Discard this unsaved local draft? The saved note on disk is kept.'));confirm.element.append(button('Discard draft',()=>{localStorage.removeItem(draftKey(doc!.id));recovery.replaceChildren();confirm.close();},'danger'));}));}
      }catch{/* A blocked or corrupt draft store must not hide a saved note. */}
      if(typeof extra.start==='number'){const snapshot=snapshotSurface(surface),start=rawToVisible(snapshot,codePointToUtf16(doc.content,extra.start)),end=rawToVisible(snapshot,codePointToUtf16(doc.content,typeof extra.end==='number'?extra.end:extra.start));surface.focus();restoreSelection(surface,start,end);window.getSelection()?.getRangeAt(0).startContainer.parentElement?.scrollIntoView({block:'center'});}
    }else{noteArea.replaceChildren(empty('Create a note to start writing and connecting ideas.'),button('Create note',()=>createNote(),'primary'));}
  }catch(error){state.textContent='Could not load note';surface.contentEditable='false';surface.replaceChildren(empty(error instanceof Error?error.message:'Could not open this note.'),button('Back to notes',()=>navigate('notes')));}
  return()=>{disposed=true;closeInteraction?.();window.clearTimeout(saveTimer);window.clearTimeout(searchTimer);window.removeEventListener('keydown',keyboard);window.removeEventListener('beforeunload',beforeUnload);if(dirty){rememberDraft(snapshotSurface(surface).raw);if(!conflict)void save();}};
}
