"use strict";
/* Click editor: the "Edycja" sidebar of /preview?embed=1, driven by the upload page (index.html).
   One view, no edit mode: the preview stays what it is (steps, answers, conditions) and the sidebar sits to its
   right. The pencil next to an element's name (preview.html's fixLink: every field, section, page heading, the title and the
   dashed line of a hidden element) opens the sidebar on that element; one click collapses it to a slim strip and the
   state is remembered. Every change is checked by the server with the real COI rules (POST /api/forms/check,
   called by the parent).

   Protocol (same-origin postMessage):
   - parent -> here {type: "coi-preview", json, evidence, doc, key}: preview.html loads the JSON and its evidence,
     then edStart makes this document editable. `doc` numbers the document; every message back carries it, so the
     parent ignores edits of a document it no longer shows. `key` names the file: the same file again keeps undo,
     redo and the selection.
   - here -> parent {type: "coi-sidebar", open, doc}: the sidebar was opened or collapsed; the parent makes room
     for it (the original pane narrows or becomes a strip; the history column and the form never give way).
   - parent -> here {type: "coi-open"}: open the sidebar on the list of problems (a click on "Do poprawy: N").
   - parent -> here {type: "coi-close"}: collapse the sidebar (the clerk brought the original back and both do not fit).
   - here -> parent {type: "coi-edit", json, evidence, version} after every change, undo and redo;
     the parent saves it in the history entry and sends back
     {type: "coi-problems", problems: [{id, message, detail}], version, doc}, or {..., failed: true} when the
     check itself did not get through (the status line then offers "Sprawdź ponownie").
   - parent -> here {type: "coi-undo"} / {type: "coi-redo"}: Ctrl+Z pressed while the parent had focus.
   - parent -> here {type: "coi-bind-dict", doc, req, code, name, items, quote, label}: "Dodaj" next to a dictionary
     proposal; the field of that proposal (paper quote first, then label) gets the catalog dictionary `code` as one
     undoable change (a text field becomes a SELECT, RADIO/CHECKBOX keep their type); answer {type: "coi-bound", req,
     doc, id, label} (id null: no such field, or a choice field whose options a condition reads).
   - parent -> here {type: "coi-show", id}: scroll to the element on its step and flash it ("Zobacz w podglądzie").

   Evidence (preview.html's `evidence`, drawn under every element by its notesOf) = {elementId: {source: "paper" |
   "model" | "code" | "clerk", quote, page, condition_quote?, consent_quotes?}} from POST /api/forms/convert?evidence=1;
   what the clerk adds gets source "clerk".
   Uses the globals of preview.html (cfg, pages, pageIdx, evidence, renderPage, h, parseQuery...): both are classic scripts. */

const ED_TYPES = [["TEXT_FIELD", "Tekst (krótki)"], ["TEXTAREA", "Tekst (długi)"], ["NUMBER", "Liczba"], ["DATEPICKER", "Data"],
  ["RADIO", "Wybór jednej opcji"], ["CHECKBOX", "Wybór wielu opcji"], ["SELECT", "Lista rozwijana"], ["FILE_UPLOADER", "Załącznik"],
  ["TEXT_BLOCK", "Tekst informacyjny (bez pola)"]];  // the types the builder emits for fields from the paper
const ED_TYPE_NAMES = Object.fromEntries([...ED_TYPES, ["SECTION", "Sekcja"], ["REPEATING_SECTION", "Sekcja powtarzalna"],
  ["CONSENT_SECTION", "Zgody i oświadczenia"], ["INFO_SECTION", "Informacja"]]);
const ED_CHOICE = ["RADIO", "CHECKBOX", "SELECT"];
const ED_FORMATS = [["", "bez sprawdzania"], ["PESEL", "PESEL"], ["NIP", "NIP"], ["REGON", "REGON"], ["ZIP_CODE", "kod pocztowy"],
  ["EMAIL", "adres e-mail"], ["EPUAP", "skrytka ePUAP"], ["ADE", "adres do e-Doręczeń"], ["SERIAL_NUMBER_ID_CARD", "seria i numer dowodu"]];
const ED_DICTS = [["", "Własne opcje (lista poniżej)"], ["Kraje", "Słownik COI: Kraje"],
  ["TERYT_4G_DOWODOWY", "Słownik COI: TERYT (województwo, powiat, gmina, miejscowość)"],
  ["Numery kierunkowe", "Słownik COI: Numery kierunkowe (telefon)"]];
const ED_DICT_EXTRA = {
  Kraje: {multiLevelTree: false, searchEnabled: true, defaultValue: ["Polska"]},
  TERYT_4G_DOWODOWY: {multiLevelTree: true, searchEnabled: true, defaultValue: []},
  "Numery kierunkowe": {multiLevelTree: false, searchEnabled: true, defaultValue: []},
};
// Our dictionaries from the catalog (preview.html's CATALOG_NAMES) for "Skąd lista"; none of them is a tree.
const edCatalogDicts = () => Object.entries(CATALOG_NAMES).map(([code, name]) => [code, `Słownik urzędu: ${name}`]);
const ED_CATALOG_EXTRA = {multiLevelTree: false, searchEnabled: true, defaultValue: []};
const ED_EMPTY_HTML = "<html><head></head><body></body></html>";

const ED_OPEN_KEY = "coi-sidebar";  // localStorage: "1" = the sidebar was left open
const ed = {on: false, open: false, failed: false, doc: 0, key: undefined, sel: null, version: 0, checked: -1, problems: [], undo: [], redo: [],
  pending: null, timer: 0, note: "", focusLabel: false};

/* ---------- where things are ---------- */
// Location of an element id: {kind: "title" | "tab" | "section" | "field", el, tab, section}.
function edFind(id) {
  if (id === "__title") return {kind: "title", el: null, tab: null};
  for (const tab of cfg.tabs) {
    if (tab.id === id) return {kind: "tab", el: tab, tab};
    for (const q of tab.questions) {
      if (q.field.id === id) return {kind: "section", el: q.field, tab};
      for (const c of q.field.repeatingSectionGrid || []) if (c.field.id === id) return {kind: "field", el: c.field, tab, section: q.field};
    }
  }
  return null;
}

// Nothing is locked: the standard pages (Dane, Kontakt i zgody, finish) and the address blocks are edited and
// removed like everything else. Only what the COI rules would refuse on import is held back, with the reason
// next to the button (edRemoveBlock).
const edFinish = loc => loc.tab?.type === "FINISH";  // this page may hold texts only (app/rules.py)

// Why this element cannot be removed (the JSON would not import), or "" when it can. The rules want one FINISH
// page at the end, at least one page before it, and at least one field on every page.
function edRemoveBlock(loc) {
  if (loc.kind === "tab") {
    if (edFinish(loc)) return "Strony końcowej nie usuniesz: każda e-usługa COI musi się nią kończyć.";
    return cfg.tabs.filter(t => t.type !== "FINISH").length > 1 ? "" : "To jedyna strona przed końcową, a e-usługa COI musi mieć co najmniej jedną.";
  }
  const count = q => q.field.type === "INFO_SECTION" ? 1 : (q.field.repeatingSectionGrid || []).length;  // fields as the rules count them
  const left = loc.tab.questions.reduce((n, q) => n + count(q), 0) - (loc.kind === "field" ? 1 : count(loc.tab.questions.find(q => q.field === loc.el)));
  if (left > 0) return "";
  return edFinish(loc) ? "Strona końcowa musi mieć choć jeden tekst, a to ostatni."
    : "Na tej stronie nie zostałoby żadne pole, a taka strona nie przejdzie importu do COI. Usuń całą stronę (ołówek przy jej tytule) albo najpierw dodaj inne pole.";
}

// Every element whose SHOW condition reads one of `fields`.
function edDependents(fields) {
  const out = [];
  eachField(f => {
    for (const s of f.states || []) {
      const c = cond(s.query);
      let hit = false;
      if (c.ast) edWalk(c.ast, n => { if (n.op === "path" && fields.includes(refField(n.ref))) hit = true; });
      if (hit && !fields.includes(f)) { out.push(f); break; }
    }
  });
  return out;
}

/* ---------- text helpers (mirror app/text.py) ---------- */
function edSlug(text, fallback = "pole", max = 60) {
  let s = String(text).replace(/ł/g, "l").replace(/Ł/g, "L").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (s.length > max) s = s.slice(0, max + 1).replace(/_[^_]*$/, "") || s.slice(0, max);
  return s || fallback;
}
const edEsc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// COI description HTML -> plain text, one line per paragraph.
function edText(html) {
  const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
  const blocks = [...doc.body.querySelectorAll("p, li, h3, h4")];
  return (blocks.length ? blocks.map(b => b.textContent) : [doc.body.textContent])
    .map(s => s.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n");
}
// Plain text -> COI description HTML (like html_paragraphs); `empty` when there is no text.
function edHtml(text, empty) {
  const paras = String(text).split("\n").map(s => s.trim()).filter(Boolean);
  return paras.length ? `<html><head></head><body>${paras.map(p => `<p>${edEsc(p)}</p>`).join("")}</body></html>` : empty;
}
const edNewId = () => crypto.randomUUID ? crypto.randomUUID()
  : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));

// Unique techName for a label (techNames are unique across the whole form).
function edTech(label, exceptId) {
  const used = new Set();
  eachField(f => { if (f.id !== exceptId) used.add(f.techName); });
  const base = edSlug(label);
  let name = base, n = 2;
  while (used.has(name)) name = `${base}_${n++}`;
  return name;
}

/* ---------- JSONata conditions (same shapes as app/builder/core.py) ---------- */
const edQ = (text, quote) => String(text).replace(/\\/g, "\\\\").split(quote).join("\\" + quote);
const edAnswerPath = (tab, sec, fld) =>
  `tabs['${edQ(tab, "'")}' in title].questions['${sec}' in field.techName].field.repeatingSectionGrid['${fld}' in field.techName].field.__userAnswer`;
const edRowsPath = (tab, sec, fld, sameRow) =>
  `tabs['${edQ(tab, "'")}' in title].questions['${sec}' in field.techName].field.__userAnswer${sameRow ? "[$repeatingSectionIndex]" : ""}['${fld}' in fieldTechName].answers`;
const edChosen = (option, path) => `("${edQ(option, '"')}" in ${path})`;

function edWalk(n, fn) {
  if (!n || typeof n !== "object") return;
  fn(n);
  for (const k of ["l", "r", "e"]) edWalk(n[k], fn);
}
// AST (from preview's parseQuery) -> JSONata text; paths keep their original text.
function edSerialize(n) {
  const wrap = x => ["or", "and"].includes(x.op) ? `(${edSerialize(x)})` : edSerialize(x);
  switch (n.op) {
    case "lit": return typeof n.v === "string" ? `"${edQ(n.v, '"')}"` : String(n.v);
    case "path": return n.raw;
    case "not": return `$not(${edSerialize(n.e)})`;
    case "or": case "and": return `${wrap(n.l)} ${n.op} ${wrap(n.r)}`;
    default: return `(${edSerialize(n.l)} ${n.op} ${edSerialize(n.r)})`;
  }
}
// Rewrites every condition in the form: visit(node) returns true when it changed the node.
function edRewrite(visit) {
  eachField(f => {
    for (const s of f.states || []) {
      let ast;
      try { ast = parseQuery(s.query); } catch (e) { continue; }
      let changed = false;
      edWalk(ast, n => { if (visit(n)) changed = true; });
      if (changed) s.query = edSerialize(ast);
    }
  });
  conds.clear();
}

// True when a SHOW condition of the element reads a field that no longer exists (its trigger was deleted).
const edBrokenCondition = f => condGone(f);  // preview.html: the dashed line and the "Widoczne, gdy" line say it the same way
// {field, option} when the element has exactly one SHOW condition of the form „pole” = „opcja”.
function edSingleCondition(f) {
  const shows = (f.states || []).filter(s => s.type === "SHOW");
  if (shows.length !== 1) return null;
  const c = cond(shows[0].query), a = c.ast;
  if (!a || a.op !== "in" || a.l.op !== "lit" || a.r.op !== "path") return null;
  const trig = refField(a.r.ref);
  return trig ? {field: trig, option: a.l.v} : null;
}

/* ---------- changes, undo, sync with the parent ---------- */
function edSnapshot() {
  return {json: JSON.stringify(cfg), evidence: JSON.stringify(evidence), sel: ed.sel, page: pageIdx};
}
function edRestore(s) {
  cfg = JSON.parse(s.json); evidence = JSON.parse(s.evidence); ed.sel = s.sel; pageIdx = s.page;
}

// One undoable change: fn() mutates cfg, then the form, the panel and the parent are updated.
// `back` = what "Cofnij" will say when it takes this change back (e.g. "Przywrócono pole „Płeć psa”.").
function edChange(fn, note = "", back = "") {
  edFlush();
  ed.undo.push({...edSnapshot(), back});
  if (ed.undo.length > 100) ed.undo.shift();
  ed.redo = [];
  ed.note = note;
  fn();
  edAfter();
}
// Typing in a text box: applied on every key, one undo step per pause, the panel keeps focus.
function edTyping(fn) {
  if (!ed.pending) ed.pending = edSnapshot();
  fn();
  ed.version++;
  edRerender();
  edStatus();
  const loc = ed.sel && edFind(ed.sel), title = $(".ed-title");  // the panel is not redrawn (focus stays), its heading follows the label
  if (loc && title) title.textContent = edName(loc);
  clearTimeout(ed.timer);
  ed.timer = setTimeout(edFlush, 500);
}
function edFlush() {
  clearTimeout(ed.timer);
  if (!ed.pending) return;
  ed.undo.push(ed.pending);
  ed.pending = null;
  ed.redo = [];
  edPost();
}
function edAfter() {
  ed.version++;
  edRerender();
  edPanel();
  edPost();
}
function edUndo() {
  edFlush();
  const s = ed.undo.pop();
  if (!s) return;
  ed.redo.push(edSnapshot());
  edRestore(s);
  ed.note = s.back || "Cofnięto ostatnią zmianę.";
  edAfter();
}
function edRedo() {
  edFlush();
  const s = ed.redo.pop();
  if (!s) return;
  ed.undo.push(edSnapshot());
  edRestore(s);
  ed.note = "Ponowiono cofniętą zmianę.";
  edAfter();
}
function edPost() {
  ed.failed = false;  // a new check is on its way
  parent.postMessage({type: "coi-edit", json: JSON.stringify(cfg, null, 2), evidence: evidence, version: ed.version, doc: ed.doc}, location.origin);
}
// Re-reads the whole config after a change and redraws the page in place (same step, same scroll).
function edRerender() {
  issues = [];
  conds.clear();
  normalize(cfg);
  pages = orderPages();
  precheck();
  const given = answers;
  resetAnswers();
  keepAnswers(given);  // the clerk's answers in the preview (a revealed section stays revealed)
  renderIssues();
  renderLegend();
  $("#formTitle").textContent = cfg.title || "Formularz bez tytułu";
  pageIdx = Math.max(0, Math.min(pageIdx, pages.length - 1));
  renderPage();
}

/* ---------- edits ---------- */
// Grid positions like the builder: one column, sections stacked, a section spans its fields + 1 rows.
function edLayoutSection(sec) {
  sec.repeatingSectionGrid = sortCells(sec.repeatingSectionGrid);
  sec.repeatingSectionGrid.forEach((c, i) => { Object.assign(c, {x: 0, y: i, rows: 1, cols: 3}); c.field.questionOrder = i; });
}
const edRenumber = cells => cells.forEach((c, k) => { c.y = k; });  // array order wins over old positions
function edLayoutTab(tab) {
  let y = 0;
  tab.questions = sortCells(tab.questions);
  for (const q of tab.questions) {
    const rows = (q.field.repeatingSectionGrid || []).length + 1;
    Object.assign(q, {x: 0, y, rows, cols: 3});
    q.field.questionOrder = y;
    y += rows;
  }
}

// `text` = a TEXT_BLOCK instead of an input: the only thing the FINISH page may hold.
function edNewField(text = false) {
  const f = {id: edNewId(), type: "TEXT_FIELD", techName: edTech(text ? "Nowy tekst" : "Nowe pole"), title: text ? "" : "Nowe pole", description: "",
    options: [], validation: [], readOnly: false, repeatingSectionGrid: [], states: [], questionOrder: 0, tooltipMessage: ""};
  if (text) { edSetType(f, "TEXT_BLOCK"); f.description = edHtml("Nowy tekst", ""); }
  return f;
}
function edNewSection(text = false, title = "Nowa sekcja") {
  const sec = {id: edNewId(), type: "SECTION", techName: edTech(title), title, description: ED_EMPTY_HTML, options: [],
    validation: [], readOnly: false, repeatingSectionGrid: [], states: [], questionOrder: 0, tooltipMessage: ""};
  const f = edNewField(text);
  f.techName = edTech(text ? "Nowy tekst" : "Nowe pole", sec.id);
  sec.repeatingSectionGrid.push({x: 0, y: 0, rows: 1, cols: 3, field: f});
  return sec;
}
const edMark = (...els) => { for (const el of els) evidence[el.id] = {source: "clerk", quote: null, page: null}; };

// Adds a new text field to section `secId`, after field `afterId` (or at the end).
function edAddField(secId, afterId) {
  edChange(() => {
    const loc = edFind(secId), sec = loc.el, f = edNewField(edFinish(loc));
    const grid = sortCells(sec.repeatingSectionGrid);
    const at = afterId ? grid.findIndex(c => c.field.id === afterId) + 1 : grid.length;
    grid.splice(at, 0, {x: 0, y: 0, rows: 1, cols: 3, field: f});
    edRenumber(grid);
    sec.repeatingSectionGrid = grid;
    edLayoutSection(sec);
    edLayoutTab(loc.tab);
    edMark(f);
    ed.sel = f.id;
    ed.focusLabel = true;
  }, "Dodano pole. Wpisz jego etykietę.");
  edShow(ed.sel);
}
// Adds a new section (with one field) to tab `tabId`, after section `afterId` (or at the end).
function edAddSection(tabId, afterId) {
  edChange(() => {
    const tab = edFind(tabId).el, sec = edNewSection(tab.type === "FINISH");
    const qs = sortCells(tab.questions);
    const at = afterId ? qs.findIndex(q => q.field.id === afterId) + 1 : qs.length;
    qs.splice(at, 0, {x: 0, y: 0, rows: 2, cols: 3, field: sec});
    edRenumber(qs);
    tab.questions = qs;
    edLayoutTab(tab);
    edMark(sec, sec.repeatingSectionGrid[0].field);
    ed.sel = sec.id;
    ed.focusLabel = true;
  }, "Dodano sekcję z jednym polem. Wpisz jej tytuł.");
  edShow(ed.sel);
}
// Adds a new page (one section with one field) right after page `tab`. The links that left `tab` leave the new page
// now and `tab` links to it, so a branching flow of an imported JSON stays as it was. Page titles stay unique: the
// rules refuse two pages with one title, and conditions find a page by its title.
function edAddTab(tab) {
  edChange(() => {
    let title = "Nowa strona";
    for (let n = 2; cfg.tabs.some(t => t.title === title); n++) title = `Nowa strona ${n}`;
    const sec = edNewSection(), page = {id: edNewId(), title, type: "MAIN", questions: [{x: 0, y: 0, rows: 2, cols: 3, field: sec}]};
    edLayoutTab(page);
    cfg.tabs.splice(cfg.tabs.indexOf(tab) + 1, 0, page);
    cfg.flow = cfg.flow || {links: []};
    cfg.flow.links = cfg.flow.links || [];
    for (const l of cfg.flow.links) if (l.source === tab.id) l.source = page.id;
    cfg.flow.links.push({id: edNewId(), source: tab.id, target: page.id});
    edMark(page, sec, sec.repeatingSectionGrid[0].field);
    ed.sel = page.id;
    ed.focusLabel = true;
  }, "Dodano stronę z jedną sekcją i jednym polem. Wpisz jej tytuł.");
  edShow(ed.sel);
}
function edMove(id, dir) {
  edChange(() => {
    const loc = edFind(id);
    const list = loc.kind === "field" ? sortCells(loc.section.repeatingSectionGrid) : sortCells(loc.tab.questions);
    const i = list.findIndex(c => c.field.id === id), j = i + dir;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    edRenumber(list);
    if (loc.kind === "field") { loc.section.repeatingSectionGrid = list; edLayoutSection(loc.section); }
    else loc.tab.questions = list;
    edLayoutTab(loc.tab);
  });
}
// Removes a field, a section with its fields, or a whole page with everything on it (one undo step).
// The only field of a section takes the emptied section with it: an empty titled section is of no use.
function edRemove(id) {
  let loc = edFind(id);
  if (!loc || loc.kind === "title" || edRemoveBlock(loc)) return;
  const name = `„${edShort(edName(loc))}”`;
  const lone = loc.kind === "field" && loc.section.repeatingSectionGrid.length === 1;
  if (lone) loc = edFind(loc.section.id);
  const inside = s => [s, ...(s.repeatingSectionGrid || []).map(c => c.field)];
  const gone = loc.kind === "tab" ? loc.tab.questions.flatMap(q => inside(q.field)) : loc.kind === "section" ? inside(loc.el) : [loc.el];
  const broken = edDependents(gone).filter(f => !gone.includes(f)), deps = broken.length;  // their conditions read what goes away
  const what = lone ? `pole ${name} i jego pustą sekcję` : {tab: `stronę ${name} razem z jej polami`, section: `sekcję ${name}`, field: `pole ${name}`}[loc.kind];
  const back = loc.kind === "tab" || lone ? "Przywrócisz to" : loc.kind === "section" ? "Przywrócisz ją" : "Przywrócisz je";
  const restored = lone ? `pole ${name} i jego sekcję` : {tab: `stronę ${name}`, section: `sekcję ${name}`, field: `pole ${name}`}[loc.kind];
  edChange(() => {
    if (loc.kind === "tab") edRemoveTab(loc.tab);
    else if (loc.kind === "field") {
      loc.section.repeatingSectionGrid = loc.section.repeatingSectionGrid.filter(c => c.field.id !== loc.el.id);
      edLayoutSection(loc.section);
    } else loc.tab.questions = loc.tab.questions.filter(q => q.field.id !== loc.el.id);
    if (loc.kind !== "tab") edLayoutTab(loc.tab);
    ed.sel = deps ? broken[0].id : loc.kind === "field" ? loc.section.id : null;
  }, `Usunięto ${what}.` + (deps ? "" : ` ${back} przyciskiem „Cofnij”.`),  // with a broken condition the panel below says what to do
     `Przywrócono ${restored}.`);
  if (deps) edShow(broken[0].id, "start");  // the element that now needs a fix, on whatever step it is: its heading and the red line under it
}
// At most 60 characters, cut between words.
const edShort = text => (text.length <= 60 ? text : text.slice(0, 58).replace(/\s+\S*$/, "") + "…");
// The one name of an element, for the panel heading and the notes about it (preview.html's nameOf: never a techName).
const edName = loc => (loc.kind === "title" ? cfg.title || "(bez tytułu)" : loc.kind === "tab" ? loc.tab.title : nameOf(loc.el));
// Brings an element into view, on its own step.
function edShow(id, block = "nearest") {
  const at = edFind(id);
  if (!at) return;
  if (at.tab && pages.indexOf(at.tab) !== pageIdx) go(pages.indexOf(at.tab), false);
  document.querySelector(`[data-fid="${CSS.escape(id)}"]`)?.scrollIntoView({block});
}

// Type change: keeps label, description and REQUIRED; drops what the new type cannot have.
function edSetType(f, type) {
  const required = validators(f).includes("REQUIRED") && type !== "TEXT_BLOCK";
  const keepFormat = type === "TEXT_FIELD" ? validators(f).filter(v => v !== "REQUIRED") : [];
  f.type = type;
  f.validation = [...(required ? ["REQUIRED"] : []), ...keepFormat];
  if (!ED_CHOICE.includes(type)) f.options = [];
  const ours = f.dictionaryName in CATALOG_NAMES && ED_CHOICE.includes(type);  // a catalog dictionary fits RADIO/CHECKBOX too
  if (!ours && (type !== "SELECT" || !f.dictionaryName)) for (const k of ["dictionaryName", "multiLevelTree"]) delete f[k];
  for (const k of ["searchEnabled", "defaultValue", "defaultValueType"]) delete f[k];
  if (ours && type !== "SELECT") delete f.multiLevelTree;
  if (type === "SELECT") Object.assign(f, f.dictionaryName ? ED_DICT_EXTRA[f.dictionaryName] || ED_CATALOG_EXTRA : {searchEnabled: f.options.length > 15, defaultValue: []});
  edCanonical(f);
}
function edSetDictionary(f, name) {
  for (const k of ["dictionaryName", "multiLevelTree", "searchEnabled", "defaultValue", "defaultValueType"]) delete f[k];
  const select = f.type === "SELECT";  // RADIO/CHECKBOX take a dictionary too, without the list's extras
  if (name) { f.options = []; Object.assign(f, {dictionaryName: name}, select ? ED_DICT_EXTRA[name] || ED_CATALOG_EXTRA : {}); }
  else if (select) Object.assign(f, {searchEnabled: f.options.length > 15, defaultValue: []});
  edCanonical(f);
}

// The field a dictionary proposal was made for: the paper quote first (one contains the other), then the label.
function edProposalField(quote, label) {
  const n = t => String(t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const q = n(quote), l = n(label);
  let best = null, top = 0;
  eachField(f => {
    if (!["TEXT_FIELD", "TEXTAREA", ...ED_CHOICE].includes(f.type)) return;
    const fq = n(evidence[f.id]?.quote), ft = n(f.title);
    // same quote > same label > one quote inside the other (a much longer quote, like a statement, never counts)
    const score = fq && fq === q ? 4 : ft && ft === l ? 3
      : fq && q && fq.length <= 2 * q.length && (q.includes(fq) || fq.includes(q)) ? 2 + fq.length / 1000 : 0;
    if (score > top) [best, top] = [f, score];
  });
  return best;
}
// "Dodaj" on the upload page: the proposal's field reads the catalog dictionary from now on (one undoable change).
function edBindDictionary(d) {
  CATALOG[d.code] = d.items || [];  // the preview has the values before it redraws, not after its next fetch
  CATALOG_NAMES[d.code] = d.name;
  let f = edProposalField(d.quote, d.label);
  // a choice field other fields depend on keeps its options: the conditions compare with their exact names
  if (f && ED_CHOICE.includes(f.type) && edDependents([f]).length) f = null;
  if (f) edChange(() => { if (!ED_CHOICE.includes(f.type)) edSetType(f, "SELECT"); edSetDictionary(f, d.code); },
    `Pole „${nameOf(f)}” bierze wartości ze słownika „${d.name}”.`);
  parent.postMessage({type: "coi-bound", req: d.req, doc: ed.doc, id: f ? f.id : null, label: f ? nameOf(f) : ""}, location.origin);
}
// Key order of the builder: base keys, then type extras, then states, questionOrder, tooltipMessage.
function edCanonical(f) {
  const head = ["id", "type", "techName", "title", "description", "options", "validation", "readOnly", "repeatingSectionGrid"];
  const tail = ["states", "questionOrder", "tooltipMessage"];
  const copy = {...f};
  for (const k of Object.keys(f)) delete f[k];
  for (const k of head) if (k in copy) f[k] = copy[k];
  for (const k of Object.keys(copy)) if (!head.includes(k) && !tail.includes(k)) f[k] = copy[k];
  for (const k of tail) if (k in copy) f[k] = copy[k];
}
function edSetValidation(f, required, format) {
  f.validation = [...(required ? ["REQUIRED"] : []), ...(format ? [format] : [])];
}

const edOption = name => ({id: name, name, techName: edSlug(name, "opcja")});
// Renames option `old` of field f and every condition that compares with it.
function edRenameOption(f, old, name) {
  edRewrite(n => {
    if (!["in", "=", "!="].includes(n.op)) return false;
    const lit = [n.l, n.r].find(x => x.op === "lit" && x.v === old), path = [n.l, n.r].find(x => x.op === "path");
    if (!lit || !path || refField(path.ref) !== f) return false;
    lit.v = name;
    return true;
  });
  f.options = f.options.map(o => (o.name === old ? edOption(name) : o));
  edSelectSize(f);
}
const edSelectSize = f => { if (f.type === "SELECT" && !f.dictionaryName) f.searchEnabled = f.options.length > 15; };

// Renames a page and every condition path that finds it by title.
function edRenameTab(tab, title) {
  const old = tab.title;
  edRewrite(n => {
    if (n.op !== "path" || n.ref.tab !== old) return false;
    n.raw = n.raw.replace(/^tabs\[\s*(['"])(?:\\.|(?!\1)[^\\])*\1\s+in\s+title\s*\]/, `tabs['${edQ(title, "'")}' in title]`);
    n.ref.tab = title;
    return true;
  });
  tab.title = title;
}

// Sets „pokaż, gdy [trigger] = [option]” on the target, or clears it (trigger null).
function edSetCondition(target, trigger, option) {
  const keep = (target.states || []).filter(s => s.type !== "SHOW");
  let query = null;
  if (trigger) {
    const t = edFind(trigger.id), mine = edFind(target.id);
    const owner = mine.kind === "field" ? mine.section : mine.el;
    query = edChosen(option, t.section.type === "REPEATING_SECTION"
      ? edRowsPath(t.tab.title, t.section.techName, trigger.techName, owner === t.section)
      : edAnswerPath(t.tab.title, t.section.techName, trigger.techName));
  }
  target.states = [...keep, ...(query ? [{type: "SHOW", query}] : [])];
  const ev = evidence[target.id];
  if (ev) {
    delete ev.condition_quote;
    if (query) ev.condition_source = "clerk"; else delete ev.condition_source;
  }
}
// Choice fields with own options a target may depend on (a section never on its own fields).
function edTriggers(loc) {
  const own = loc.kind === "section" ? (loc.el.repeatingSectionGrid || []).map(c => c.field) : [];
  const out = [];
  for (const tab of pages) for (const q of tab.questions) for (const c of q.field.repeatingSectionGrid || []) {
    const f = c.field;
    if (ED_CHOICE.includes(f.type) && !f.dictionaryName && (f.options || []).length && f !== loc.el && !own.includes(f))
      out.push({f, tab, section: q.field});
  }
  return out;
}

// Renaming a field or section the clerk added (nothing depends on it yet) also renames its techName.
function edRetech(el) {
  if (evidence[el.id]?.source !== "clerk" || edDependents([el]).length) return;
  if (el.type === "SECTION" && edDependents((el.repeatingSectionGrid || []).map(c => c.field)).length) return;
  el.techName = edTech(el.title || "pole", el.id);
}

// Takes a page out of the form and joins its neighbours in flow.links (pages are one chain, like the builder makes it).
function edRemoveTab(tab) {
  const i = pages.indexOf(tab), prev = pages[i - 1], next = pages[i + 1];
  cfg.tabs = cfg.tabs.filter(t => t !== tab);
  cfg.flow = cfg.flow || {links: []};
  cfg.flow.links = (cfg.flow.links || []).filter(l => l.source !== tab.id && l.target !== tab.id);
  if (prev && next) cfg.flow.links.push({id: edNewId(), source: prev.id, target: next.id});
}

/* ---------- the form in edit mode ---------- */
const edProblemsOf = id => ed.problems.filter(p => p.id === id);

// After every render: the selected element and the problems are marked on the form. Nothing else changes in it:
// the preview stays a preview (the paper quotes, conditions and pencils are drawn by preview.html).
function edDecorate() {
  const tab = pages[pageIdx], page = $("#page");
  const head = $("#pageHeading"), title = $("#formTitle");
  head.dataset.fid = tab.id;
  title.dataset.fid = "__title";
  for (const el of [title, ...page.querySelectorAll("[data-fid]")]) {
    const id = el.dataset.fid;
    el.classList.toggle("ed-sel", id === ed.sel);
    el.classList.toggle("ed-bad", edProblemsOf(id).length > 0);
    const probs = edProblemsOf(id).map(p => h("p", {class: "ed-prob"}, "⚠ " + p.message));
    if (!probs.length || id === "__title") continue;
    if (el === head) el.after(...probs);
    else if (el.matches("section.card")) ($(":scope > h3", el) || el.firstChild).after(...probs);
    else el.append(...probs);
  }
  // stepper: a red mark on pages that hold problems
  const bad = new Set(ed.problems.map(p => p.id && edFind(p.id)?.tab).filter(Boolean));
  $("#stepNav").querySelectorAll(".stepper li").forEach((li, i) => {
    if (bad.has(pages[i])) li.append(h("span", {class: "ed-dot", title: "Na tej stronie są problemy do poprawy"}, "●", h("span", {class: "sr-only"}, " (do poprawy)")));
  });
}

/* ---------- side panel ---------- */
function edEnsurePanel() {
  if ($("#edPanel")) return;
  document.head.append(h("style", {}, ED_CSS));
  document.body.append(h("aside", {id: "edPanel", "aria-label": "Edycja formularza"},
    // collapsed: one slim strip, a click opens the sidebar
    h("button", {type: "button", id: "edStrip", title: "Rozwiń panel edycji", onclick: () => edSetOpen(true)},
      h("span", {"aria-hidden": "true"}, "‹"), h("span", {class: "ed-strip-label"}, "Edycja"), h("span", {class: "ed-dot", id: "edStripBad", hidden: true}, "●")),
    h("div", {class: "ed-top"},
      h("div", {class: "ed-head"}, h("h2", {}, "Edycja"),
        h("button", {type: "button", class: "ed-btn", id: "edUndo", title: "Cofnij ostatnią zmianę", onclick: edUndo}, "Cofnij"),
        h("button", {type: "button", class: "ed-btn", id: "edRedo", title: "Ponów cofniętą zmianę", onclick: edRedo}, "Ponów"),
        h("button", {type: "button", class: "ed-btn", id: "edClose", title: "Zwiń panel edycji", "aria-label": "Zwiń panel edycji", onclick: () => edSetOpen(false)}, "›")),
      h("div", {id: "edStatus", role: "status"})),
    h("div", {id: "edBody"})));
}

// Opens or collapses the sidebar (remembered). `pin` = the element that must stay where it is on screen while
// the layout changes around it (the parent makes room, the form gets narrower or wider); default: the selected
// element if it is in view, else the first one in view.
function edSetOpen(open, pin) {
  if (ed.open === open) return;
  const inView = x => x.getClientRects().length && x.getBoundingClientRect().top >= 0 && x.getBoundingClientRect().top < innerHeight;
  const el = pin || [...$("#page").querySelectorAll(".ed-sel")].find(inView)  // the element being edited, when it is on screen
    || [...$("#page").querySelectorAll("[data-fid]")].find(inView) || $("#pageHeading");
  const top = el.getBoundingClientRect().top;
  ed.open = open;
  try { localStorage.setItem(ED_OPEN_KEY, open ? "1" : ""); } catch (e) { /* private mode: the state just is not remembered */ }
  document.body.classList.toggle("sidebar", open);
  if (open && !($("#notesToggle").checked && $("#condsToggle").checked)) { $("#notesToggle").checked = $("#condsToggle").checked = true; applyModes(); }  // editing works on the annotated form
  parent.postMessage({type: "coi-sidebar", open, doc: ed.doc}, location.origin);
  const keep = () => { if (el.isConnected) window.scrollBy(0, el.getBoundingClientRect().top - top); };
  keep();
  const watch = new ResizeObserver(keep);  // the parent resizes this frame a moment later
  watch.observe(document.documentElement);
  setTimeout(() => watch.disconnect(), 600);
  if (open) edPanel(); else edFlush();
}

// The pencil next to an element's name (preview.html): the sidebar opens on it, its label ready to type in; the element stays in place.
function edOpen(id) {
  if (!ed.on) return;
  edSetOpen(true, document.querySelector(`[data-fid="${CSS.escape(id)}"]`));
  edSelect(id);
  edFocusLabel();
}

// Status line, undo/redo buttons and the problem lists; never touches the inputs (focus stays).
function edStatus() {
  const n = ed.problems.length, box = $("#edStatus");
  const waiting = ed.checked !== ed.version && !ed.failed;
  box.className = "ed-status " + (ed.failed ? "bad" : waiting ? "wait" : n ? "bad" : "ok");
  // every state is one line of the same height, so nothing below it moves while a check runs
  box.title = ed.failed ? "Nie udało się sprawdzić zmian: brak połączenia z serwerem. Pobieranie JSON-a jest wstrzymane."
    : n ? "Pobieranie JSON-a jest zablokowane, dopóki są problemy." : "";
  box.replaceChildren(ed.failed ? "Brak połączenia. "
    : waiting ? "Sprawdzam…"
    : n ? `Do poprawy: ${n} ${plural(n, ["problem", "problemy", "problemów"])}. `
    : "Zgodne z regułami COI.",
    ed.failed ? h("button", {type: "button", class: "ed-link", onclick: () => { edPost(); edStatus(); }}, "Sprawdź ponownie")
    : !waiting && n && ed.sel ? h("button", {type: "button", class: "ed-link", onclick: () => edSelect(null)}, "Pokaż listę") : "");
  $("#edStripBad").hidden = !n && !ed.failed;  // the collapsed strip shows that something waits
  $("#edUndo").disabled = !ed.undo.length && !ed.pending;
  $("#edRedo").disabled = !ed.redo.length;
  const mine = $("#edMine");
  if (mine) mine.replaceChildren(...edProblemsOf(ed.sel).map(p => h("li", {title: p.detail}, p.message)));
  const all = $("#edAll");
  if (all) {
    all.replaceChildren(...ed.problems.map(p => {
      const loc = p.id && edFind(p.id);
      const where = !loc ? "Cały formularz" : loc.kind === "title" ? "Tytuł e-usługi" : loc.kind === "tab" ? `Strona „${loc.tab.title}”`
        : `${loc.tab.title} › ${nameOf(loc.el)}`;
      return h("li", {title: p.detail}, loc ? h("button", {type: "button", class: "ed-link", onclick: () => edReveal(p.id)}, where) : h("strong", {}, where),
        h("br"), p.message);
    }));
    $("#edAllWrap").hidden = !ed.problems.length;
    $("#edHelp").hidden = ed.problems.length > 0;  // with problems the list is the whole message
  }
}

// Shows an element (a link in the list of problems): its step, selection, scroll.
function edReveal(id) {
  if (!edFind(id)) return;
  edSelect(id);
  edShow(id, "center");
  edMarkSelected();  // after a step change the page was redrawn
}
function edSelect(id) {
  edFlush();
  ed.sel = id;
  ed.note = "";
  edMarkSelected();
  edPanel();
}
function edMarkSelected() {
  for (const el of document.querySelectorAll("[data-fid]")) el.classList.toggle("ed-sel", el.dataset.fid === ed.sel);
}

// Small form controls for the panel. `key` lets focus come back to the same control after a redraw.
let edUid = 0;
function edRow(label, control, hint) {
  const id = control.id || (control.id = "ed" + (++edUid));
  return h("div", {class: "ed-row"}, h("label", {for: id}, label), control, hint ? h("p", {class: "ed-hint"}, hint) : null);
}
function edInput(value, key, onTyping, onCommit) {
  const inp = h("input", {type: "text", "data-k": key, autocomplete: "off"});
  inp.value = value;
  if (onTyping) inp.addEventListener("input", () => edTyping(() => onTyping(inp.value)));
  inp.addEventListener("change", () => (onCommit ? onCommit(inp) : edFlush()));
  inp.addEventListener("keydown", e => { if (e.key === "Enter") inp.blur(); });
  return inp;
}
function edArea(value, key, onTyping) {
  const ta = h("textarea", {"data-k": key, rows: "3"});
  ta.value = value;
  ta.addEventListener("input", () => edTyping(() => onTyping(ta.value)));
  ta.addEventListener("change", edFlush);
  return ta;
}
function edSelectBox(options, value, key, onChange) {
  const sel = h("select", {"data-k": key}, options.map(([v, label]) => h("option", {value: v}, label)));
  sel.value = value;
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}
const edBtn = (text, key, onclick, opts = {}) =>
  h("button", {type: "button", class: "ed-btn" + (opts.danger ? " danger" : ""), "data-k": key, disabled: opts.disabled,
    "aria-label": opts.label, title: opts.label, onclick}, text);

function edPanel() {
  const body = $("#edBody");
  if (!body) return;
  const focusKey = document.activeElement?.closest?.("#edPanel") ? document.activeElement.dataset.k : null;
  const loc = ed.sel ? edFind(ed.sel) : null;
  if (ed.sel && !loc) ed.sel = null;
  const note = ed.note ? h("p", {class: "ed-note", role: "status"}, ed.note) : null;
  const sameElement = ed.panelFor === ed.sel;  // focus returns to a control only on the element it belonged to:
  ed.panelFor = ed.sel;                        // after "Usuń pole" it must not land on the section's "Usuń sekcję"
  body.replaceChildren(note || "", ...(loc ? edElementPanel(loc) : edHelpPanel()));
  edStatus();
  if (ed.focusLabel) { ed.focusLabel = false; const l = $("[data-k=label]", body); if (l) { l.focus(); l.select(); } }
  else if (focusKey && sameElement) $(`[data-k="${CSS.escape(focusKey)}"]`, body)?.focus({preventScroll: Boolean(note)});
  if (note || !sameElement) $("#edPanel").scrollTop = 0;  // another element, or a note about what just happened: from the top
}

function edHelpPanel() {
  return [
    h("div", {id: "edAllWrap", hidden: true}, h("ol", {class: "ed-probs", id: "edAll", "aria-label": "Do poprawy"})),  // the banner above already says "Do poprawy: N"
    h("div", {id: "edHelp"},
      h("p", {class: "ed-lead"}, "Kliknij ołówek przy nazwie elementu formularza, aby go poprawić albo usunąć.")),
  ];
}

function edEvidenceBox(loc) {
  if (loc.kind === "title" || (loc.kind === "tab" && isStandard(loc.tab))) return null;  // a standard page says so in its own line on the form
  const ev = evidence[loc.el.id], box = h("div", {class: "ed-evidence"});
  const quote = (label, text) => h("p", {}, h("strong", {}, label + ": "), `„${text}”`);
  if (!ev) box.append(h("p", {}, "Brak informacji o pochodzeniu (wynik bez cytatów: starszy albo wczytany z pliku)."));
  else if (ev.source === "paper" && ev.quote) {
    box.classList.add("paper");
    box.append(quote("Z dokumentu", ev.quote));
  } else if (ev.source === "code" && (ev.consent_quotes || []).some(Boolean)) {
    box.append(h("p", {}, "Zgody ze Standardów COI. Oświadczenia z dokumentu mają cytat poniżej."));
  } else if (ev.source !== "model") {  // a page or section the model grouped has nothing to cite: no line (the legend says it once)
    box.append(h("p", {}, {
      code: "Ze Standardów COI, nie z dokumentu.",
      clerk: "Dodane przez Ciebie, bez cytatu z dokumentu.",
      paper: "Z dokumentu, bez zapisanego cytatu.",
    }[ev.source] || "Brak informacji o pochodzeniu."));
  }
  if (ev?.condition_quote) box.append(quote("Warunek z dokumentu", ev.condition_quote));
  if (ev?.condition_source === "clerk") box.append(h("p", {}, "Warunek ustawiony przez Ciebie."));
  return box.childElementCount ? box : null;  // the quote of each statement is next to it in edConsentControls
}

function edElementPanel(loc) {
  const kind = {title: "Tytuł e-usługi", tab: "Strona", section: ED_TYPE_NAMES[loc.el?.type] || "Sekcja", field: "Pole"}[loc.kind];
  const name = edName(loc);
  // "Usuń" sits in the heading row of every element; when the rules would refuse the result, it is off and says why
  const block = loc.kind === "title" ? "" : edRemoveBlock(loc);
  const del = loc.kind !== "title" && edBtn({tab: "Usuń stronę", section: "Usuń sekcję", field: "Usuń pole"}[loc.kind], "del",
    () => edRemove(loc.kind === "tab" ? loc.tab.id : loc.el.id), {danger: true, disabled: Boolean(block)});
  const out = [h("div", {class: "ed-kind-row"}, h("p", {class: "ed-kind"}, kind), del),
    h("h3", {class: "ed-title"}, name), block ? h("p", {class: "ed-hint ed-block"}, block) : "",
    edEvidenceBox(loc) || "", ed.note ? "" : h("ul", {class: "ed-probs", id: "edMine"})];
  if (loc.kind === "title") out.push(edRow("Tytuł e-usługi", edInput(cfg.title || "", "label", v => { cfg.title = v; }),
    "Zaczyna się od „Wniosek”, „Zgłoszenie”, „Deklaracja” albo „Oświadczenie”."));
  else if (loc.kind === "tab") out.push(...edTabControls(loc));
  else if (loc.kind === "section") out.push(...edSectionControls(loc));
  else out.push(...edFieldControls(loc));
  if (loc.el && edBrokenCondition(loc.el)) {  // "Kiedy widoczne" moves right under the title: it is the thing to fix
    const at = out.findIndex(n => n.classList?.contains("ed-cond-box"));
    if (at > 2) out.splice(2, 0, ...out.splice(at, 1));
  }
  return out;
}

// The addressee named on the paper is the fixed value of this Dane field.
function edAddresseeRow(f) {
  return edRow("Adresat wniosku (urząd lub organ)", edInput(String(f.defaultValue ?? ""), "addressee", v => { f.defaultValue = v; }),
    "Obywatel widzi go jako stałą wartość. Puste: urząd uzupełni adresata sam.");
}

// The consents of a CONSENT_SECTION: the RODO clause from the Standards and the statements from the paper
// ("Oświadczam, że ..."), each with its text, "Wymagane" and "Usuń". In evidence.consent_quotes a quote = from the
// paper, null = the standard clause, false = added here.
function edConsentControls(f) {
  const list = f.consentSectionConfig.consents, quotes = () => evidence[f.id]?.consent_quotes;
  const items = list.map((c, i) => {
    const cb = h("input", {type: "checkbox", "data-k": `consentreq${i}`, id: "ed" + (++edUid)});
    cb.checked = c.required === true;
    cb.addEventListener("change", () => edChange(() => { c.required = cb.checked; }));
    const ta = edArea(edText(c.description), `consent${i}`, v => { c.description = edHtml(v, ""); });
    ta.setAttribute("aria-label", `Treść oświadczenia ${i + 1}`);
    const q = quotes()?.[i];
    return h("li", {}, h("span", {class: "ed-label"}, `${i + 1}. ${q === null ? "Klauzula RODO (standard COI)" : "Oświadczenie"}`), ta,
      q ? h("p", {class: "ed-hint"}, h("strong", {}, "Z dokumentu: "), `„${q}”`) : q === false ? h("p", {class: "ed-hint"}, "Dodane przez Ciebie, bez cytatu.") : "",
      h("div", {class: "ed-consent-row"}, h("span", {class: "ed-check"}, cb, h("label", {for: cb.id}, "Wymagane")),
        edBtn("Usuń", `consentdel${i}`, () => edChange(() => { list.splice(i, 1); quotes()?.splice(i, 1); }),
          {danger: true, disabled: list.length === 1, label: `Usuń oświadczenie ${i + 1}`})),
      // the rules refuse a consent section without consents: the last one goes only with the whole field
      list.length === 1 ? h("p", {class: "ed-hint"}, "To ostatnie oświadczenie. Całe pole usuniesz przyciskiem „Usuń pole” u góry.") : "");
  });
  const add = edBtn("+ Dodaj oświadczenie", "consentadd", () => edChange(() => {
    list.push({description: edHtml("Oświadczam, że ", ""), required: true});
    quotes()?.push(false);
  }));
  return h("div", {class: "ed-row"}, h("ol", {class: "ed-consents"}, items), add);
}

function edTabControls(loc) {
  const tab = loc.tab, msg = h("p", {class: "ed-hint ed-err", role: "alert"});
  const inp = edInput(tab.title, "label", null, el => {
    const v = el.value.trim();
    msg.textContent = !v ? "Tytuł strony nie może być pusty." : cfg.tabs.some(t => t !== tab && t.title === v) ? "Taki tytuł ma już inna strona." : "";
    if (msg.textContent) { el.value = tab.title; return; }
    if (v !== tab.title) edChange(() => edRenameTab(tab, v));
  });
  return [edRow("Tytuł strony", inp), msg,
    h("div", {class: "ed-actions"}, edBtn("+ Dodaj sekcję na końcu strony", "addsec", () => edAddSection(tab.id, null)),
      tab.type !== "FINISH" && edBtn("+ Dodaj stronę po tej", "addtab", () => edAddTab(tab)))];
}

function edSectionControls(loc) {
  const s = loc.el, list = sortCells(loc.tab.questions), i = list.findIndex(q => q.field === s);
  const kids = (s.repeatingSectionGrid || []).map(c => c.field);
  const loneText = kids.length === 1 && STATIC_TYPES.includes(kids[0].type) && !kids[0].title ? kids[0] : null;
  const out = [
    edRow("Tytuł sekcji", edInput(s.title || "", "label", v => { s.title = v; edRetech(s); })),
    edRow("Opis pod tytułem", edArea(edText(s.description), "desc", v => { s.description = edHtml(v, ED_EMPTY_HTML); }), "Krótko i prostym językiem, opcjonalnie."),
  ];
  if (s.type === "REPEATING_SECTION") out.push(h("p", {class: "ed-hint"}, "Sekcja powtarzalna: obywatel może dodać kolejne pozycje (np. kolejnego psa)."));
  out.push(edConditionControls(loc), h("div", {class: "ed-actions"},
    edBtn("↑ W górę", "up", () => edMove(s.id, -1), {disabled: i <= 0, label: "Przesuń sekcję w górę"}),
    edBtn("↓ W dół", "down", () => edMove(s.id, 1), {disabled: i >= list.length - 1, label: "Przesuń sekcję w dół"}),
    // an INFO_SECTION holds no fields (the rules skip its children); a section that is one text without a heading
    // has no pencil of its own on that text, so its panel leads there
    s.type !== "INFO_SECTION" && edBtn(edFinish(loc) ? "+ Tekst" : "+ Pole", "addfield", () => edAddField(s.id, null),
      {label: edFinish(loc) ? "Dodaj tekst na końcu tej sekcji" : "Dodaj pole na końcu tej sekcji"}),
    edBtn("+ Sekcja", "addsec", () => edAddSection(loc.tab.id, s.id), {label: "Dodaj sekcję poniżej tej"}),
    loneText && edBtn("Edytuj tekst sekcji", "text", () => { edSelect(loneText.id); edFocusLabel(); })));
  return out;
}

function edFieldControls(loc) {
  const f = loc.el, t = f.type, list = sortCells(loc.section.repeatingSectionGrid), i = list.findIndex(c => c.field === f);
  const vs = validators(f), required = vs.includes("REQUIRED"), format = vs.find(v => v !== "REQUIRED") || "";
  const finish = edFinish(loc), allowed = finish ? ED_TYPES.filter(([v]) => v === "TEXT_BLOCK") : ED_TYPES;  // the FINISH page: texts only
  const types = allowed.some(([v]) => v === t) ? allowed : [...allowed, [t, ED_TYPE_NAMES[t] || t]];
  const move = h("div", {class: "ed-actions"},
    edBtn("↑ W górę", "up", () => edMove(f.id, -1), {disabled: i <= 0, label: "Przesuń pole w górę"}),
    edBtn("↓ W dół", "down", () => edMove(f.id, 1), {disabled: i >= list.length - 1, label: "Przesuń pole w dół"}),
    edBtn(finish ? "+ Tekst poniżej" : "+ Pole poniżej", "addfield", () => edAddField(loc.section.id, f.id)));
  const labelInput = edInput(f.title || "", "label", v => { f.title = v; edRetech(f); });
  if (t === "CONSENT_SECTION") labelInput.placeholder = "Zgody";  // what the form shows while the title is empty
  const label = edRow(t === "TEXT_BLOCK" ? "Nagłówek (opcjonalny)" : t === "CONSENT_SECTION" ? "Tytuł" : "Etykieta", labelInput);
  // consents are a list of their own: no type, format or options to choose
  if (t === "CONSENT_SECTION" && Array.isArray(f.consentSectionConfig?.consents)) return [label, edConsentControls(f), edConditionControls(loc), move];
  const out = [
    label,
    ...(ADDRESSEE_FIELDS.includes(f.techName) ? [edAddresseeRow(f)] : []),
    edRow("Typ", edSelectBox(types.map(([v, l]) => [v, l]), t, "type", v => edChange(() => edSetType(f, v),
      ED_CHOICE.includes(v) && !ED_CHOICE.includes(t) ? "Zmieniono typ. Dodaj opcje do wyboru." : "")),
      finish ? "Na stronie końcowej COI dopuszcza tylko teksty." : null),
  ];
  if (t !== "TEXT_BLOCK") {
    const cb = h("input", {type: "checkbox", "data-k": "req"});
    cb.checked = required;
    cb.addEventListener("change", () => edChange(() => edSetValidation(f, cb.checked, t === "TEXT_FIELD" ? format : "")));
    out.push(h("div", {class: "ed-row ed-check"}, cb, h("label", {for: cb.id = "ed" + (++edUid)}, "Pole wymagane")));
  }
  if (t === "TEXT_FIELD") {
    const formats = ED_FORMATS.some(([v]) => v === format) ? ED_FORMATS : [...ED_FORMATS, [format, format]];
    out.push(edRow("Sprawdzanie formatu", edSelectBox(formats, format, "format", v => edChange(() => edSetValidation(f, required, v)))));
  }
  const ours = edCatalogDicts();
  if (t === "SELECT" || (ED_CHOICE.includes(t) && (ours.length || f.dictionaryName))) {
    const all = t === "SELECT" ? [...ED_DICTS, ...ours] : [ED_DICTS[0], ...ours];  // COI's lists are drop-down lists
    const dicts = all.some(([v]) => v === (f.dictionaryName || "")) ? all : [...all, [f.dictionaryName, `Słownik: ${f.dictionaryName}`]];
    out.push(edRow("Skąd lista", edSelectBox(dicts, f.dictionaryName || "", "dict", v => edChange(() => edSetDictionary(f, v)))));
  }
  if (ED_CHOICE.includes(t) && !f.dictionaryName) out.push(edOptionsControls(f));
  out.push(edRow(t === "TEXT_BLOCK" ? "Treść" : "Podpowiedź pod etykietą", edArea(edText(f.description), "desc", v => { f.description = edHtml(v, ""); }),
    t === "TEXT_BLOCK" ? null : "Opcjonalna, prostym językiem."));
  out.push(edConditionControls(loc), move);
  return out;
}

function edOptionsControls(f) {
  const msg = h("p", {class: "ed-hint ed-err", role: "alert"});
  const items = (f.options || []).map((o, i) => {
    const inp = edInput(o.name || "", `opt${i}`, null, el => {
      const v = el.value.trim();
      msg.textContent = !v ? "Opcja musi mieć nazwę. Aby ją usunąć, użyj ×." : f.options.some((x, k) => k !== i && x.name === v) ? "Taka opcja już jest." : "";
      if (msg.textContent) { el.value = o.name; return; }
      if (v !== o.name) edChange(() => edRenameOption(f, o.name, v));
    });
    inp.setAttribute("aria-label", `Opcja ${i + 1}`);
    return h("li", {}, inp,
      edBtn("↑", `optup${i}`, () => edChange(() => { [f.options[i - 1], f.options[i]] = [f.options[i], f.options[i - 1]]; }), {disabled: i === 0, label: `Opcja ${i + 1} w górę`}),
      edBtn("↓", `optdown${i}`, () => edChange(() => { [f.options[i + 1], f.options[i]] = [f.options[i], f.options[i + 1]]; }), {disabled: i === f.options.length - 1, label: `Opcja ${i + 1} w dół`}),
      edBtn("×", `optdel${i}`, () => edChange(() => { f.options.splice(i, 1); edSelectSize(f); }), {danger: true, label: `Usuń opcję „${o.name}”`}));
  });
  const add = edBtn("+ Dodaj opcję", "optadd", () => {
    let name = "Nowa opcja", n = 2;
    while (f.options.some(o => o.name === name)) name = `Nowa opcja ${n++}`;
    edChange(() => { f.options.push(edOption(name)); edSelectSize(f); });
    const last = $(`[data-k="opt${f.options.length - 1}"]`);
    if (last) { last.focus(); last.select(); }
  });
  return h("div", {class: "ed-row"}, h("span", {class: "ed-label"}, "Opcje do wyboru"),
    h("ol", {class: "ed-opts"}, items), msg, add,
    h("p", {class: "ed-hint"}, "Nazwę opcji zmieniasz po Enter; warunki, które jej używają, zmienią się same."));
}

function edConditionControls(loc) {
  const target = loc.el, single = edSingleCondition(target), text = condText(target);
  const triggers = edTriggers(loc);
  const box = h("div", {class: "ed-row ed-cond-box"}, h("span", {class: "ed-label"}, "Kiedy widoczne"));
  const shown = single && triggers.some(t => t.f === single.field);
  const other = Boolean(text) && !shown, broken = edBrokenCondition(target);  // other: not a plain „pole” = „opcja” the selects can show
  if (other) box.append(h("p", {class: "ed-hint" + (broken ? " ed-err" : "")}, broken
    ? "Warunek zależy od pola, którego już nie ma. Wybierz inne albo „nie zależy od innego pola”."  // the rules' text (app/problems.py) + what to do
    : `Teraz: widoczne, gdy ${text}. Wybór poniżej zastąpi ten warunek.`));
  const groups = new Map();
  for (const t of triggers) {
    if (!groups.has(t.tab)) groups.set(t.tab, []);
    groups.get(t.tab).push(t);
  }
  const fieldSel = h("select", {"data-k": "condfield", "aria-label": "Zależy od pola"},
    other && h("option", {value: "-", disabled: true}, broken ? "obecny warunek (do poprawy)" : "obecny warunek"),  // so that "nie zależy" is a real change
    h("option", {value: ""}, "nie zależy od innego pola"),
    [...groups].map(([tab, list]) => h("optgroup", {label: tab.title}, list.map(t => h("option", {value: t.f.id}, `${nameOf(t.f)} (${nameOf(t.section)})`)))));
  fieldSel.value = shown ? single.field.id : other ? "-" : "";
  fieldSel.addEventListener("change", () => {
    const trig = triggers.find(t => t.f.id === fieldSel.value)?.f;
    edChange(() => edSetCondition(target, trig || null, trig ? optionsOf(trig)[0] : null),
      trig ? "Ustawiono warunek. Wybierz opcję, przy której element ma się pokazać." : "Usunięto warunek: element nie zależy od innego pola.");
  });
  box.append(h("div", {class: "ed-cond-line"}, h("span", {}, "Zależy od"), fieldSel));
  if (shown) {
    const opts = optionsOf(single.field).map(o => [o, o]);
    if (!opts.some(([o]) => o === single.option)) opts.push([single.option, `${single.option} (tej opcji już nie ma)`]);
    const optSel = edSelectBox(opts, single.option, "condopt",
      v => edChange(() => edSetCondition(target, single.field, v)));
    optSel.setAttribute("aria-label", "Widoczne, gdy wybrano");
    box.append(h("div", {class: "ed-cond-line"}, h("span", {}, "gdy wybrano"), optSel));
  }
  if (!triggers.length) box.append(h("p", {class: "ed-hint"}, "W formularzu nie ma pola wyboru z opcjami, od którego mógłby zależeć ten element."));
  return box;
}

/* ---------- start, messages, keys ---------- */
// A document arrived (preview.html has loaded it): it becomes editable. `key` names the file: the same file
// again keeps undo, redo and the selection. The sidebar comes back the way it was left (open or collapsed).
function edStart(doc, key) {
  ed.on = Boolean(cfg) && !$("#app").hidden;  // a JSON that did not load: preview shows the error, nothing to edit
  document.body.classList.toggle("ed-on", ed.on);
  if (!ed.on) {  // no sidebar here: the parent gives the original its room back (it remembered "open" from the last file)
    parent.postMessage({type: "coi-sidebar", open: false, doc}, location.origin);
    return;
  }
  if (key === undefined || key !== ed.key) Object.assign(ed, {undo: [], redo: [], sel: null});
  Object.assign(ed, {failed: false, doc, key, version: 0, checked: -1, problems: [], pending: null, note: ""});
  if (ed.sel && !edFind(ed.sel)) ed.sel = null;
  edEnsurePanel();
  let open = false;
  try { open = localStorage.getItem(ED_OPEN_KEY) === "1"; } catch (e) { /* private mode */ }
  ed.open = !open;  // so that edSetOpen applies the state and tells the parent, also for a new document
  edSetOpen(open);
  renderPage();
  edPanel();
}
const edFocusLabel = () => $("[data-k=label]", $("#edBody"))?.focus({preventScroll: true});

{
  // Wrap the preview's render instead of editing it: marks after each render.
  const baseRender = renderPage;
  renderPage = () => { baseRender(); if (ed.on) edDecorate(); };
}

window.addEventListener("message", e => {
  if (e.origin !== location.origin) return;
  const d = e.data || {};
  if (d.type === "coi-preview") edStart(d.doc, d.key);
  if (!ed.on) return;
  if (d.type === "coi-open") { edSetOpen(true); edSelect(null); }
  if (d.type === "coi-close") edSetOpen(false);
  if (d.type === "coi-problems" && d.doc === ed.doc && d.version === ed.version && d.failed) { ed.failed = true; edStatus(); }
  else if (d.type === "coi-problems" && d.doc === ed.doc && d.version === ed.version) {
    ed.failed = false;
    // rules name no element for the form title: pin those on the title (h1)
    ed.problems = (Array.isArray(d.problems) ? d.problems : []).map(p => (!p.id && /^title /.test(p.detail || "") ? {...p, id: "__title"} : p));
    ed.checked = d.version;
    renderPage();
    edStatus();
  }
  if (d.type === "coi-bind-dict" && d.doc !== ed.doc) parent.postMessage({type: "coi-bound", req: d.req, doc: d.doc, id: null, stale: true}, location.origin);
  else if (d.type === "coi-bind-dict") edBindDictionary(d);
  if (d.type === "coi-show") {
    edShow(d.id, "center");
    const el = document.querySelector(`[data-fid="${CSS.escape(d.id)}"]`);
    if (el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
  }
  if (d.type === "coi-undo") edUndo();
  if (d.type === "coi-redo") edRedo();
});

// The citizen's view (both annotation switches off) has no pencils, so the sidebar steps aside; opening it turns them on again.
for (const id of ["#notesToggle", "#condsToggle"])
  $(id).addEventListener("change", () => { if (ed.on && !$("#notesToggle").checked && !$("#condsToggle").checked) edSetOpen(false); });

// Focus leaving the frame (a click on "JSON" or "Pobierz JSON" in the upload page): what was being typed goes out first.
window.addEventListener("blur", () => { if (ed.on) edFlush(); });

document.addEventListener("keydown", e => {
  if (!ed.on) return;
  const inText = e.target.closest?.("input, textarea, select");
  const key = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && !inText && (key === "z" || key === "y")) {
    e.preventDefault();
    if (key === "y" || e.shiftKey) edRedo(); else edUndo();
  } else if (e.key === "Escape" && !inText && ed.sel) edSelect(null);
});

const ED_CSS = `
body.ed-on{padding-right:40px}
body.ed-on .issues{display:none}  /* the sidebar lists what the COI rules found; the preview's own notes would say it twice */
body.ed-on.sidebar{padding-right:340px}
#edPanel{position:fixed;top:0;right:0;bottom:0;width:40px;background:#fff;border-left:1px solid #c5ccd6;box-shadow:-4px 0 16px rgba(16,24,40,.06);overflow:auto;font-size:15px;z-index:20}
body:not(.ed-on) #edPanel{display:none}
body.sidebar #edPanel{width:340px}
body:not(.sidebar) #edPanel>:not(#edStrip),body.sidebar #edStrip{display:none}
#edStrip{display:flex;flex-direction:column;align-items:center;gap:.6rem;width:100%;height:100%;padding:.8rem 0;background:#f8fafc;border:0;color:#003a80;font:inherit;font-size:15px;font-weight:700;cursor:pointer}
#edStrip:hover{background:#e8f0fb}
.ed-strip-label{writing-mode:vertical-rl}
#edClose{min-width:34px;font-size:18px;line-height:1;padding:.2rem .5rem}
.ed-top{position:sticky;top:0;z-index:1;background:#fff}
.ed-head{display:flex;align-items:center;gap:.5rem;padding:.6rem 1rem;border-bottom:1px solid #e3e7ec}
.ed-head h2{font-size:1.05rem;margin:0 auto 0 0}
.ed-btn{background:#fff;color:#003a80;border:1px solid #003a80;border-radius:4px;padding:.3rem .65rem;font:inherit;font-size:14px;cursor:pointer;min-height:34px}
.ed-btn:hover{background:#e8f0fb}
.ed-btn:disabled{opacity:.4;cursor:default;background:#fff}
.ed-btn.danger{color:#b3261e;border-color:#b3261e}
.ed-btn.danger:hover{background:#fdecea}
.ed-status{height:2.4rem;line-height:2.4rem;padding:0 1rem;font-size:14px;font-weight:600;border-bottom:1px solid #e3e7ec;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ed-status.ok{background:#e6f4ea;color:#14532d}
.ed-status.bad{background:#fdecea;color:#8c1d18}
.ed-status.wait{background:#f3f4f6;color:#4b5563}
#edBody{padding:1rem}
.ed-lead{font-weight:600;margin:0 0 .5rem}
.ed-kind{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#4b5563;margin:0}
.ed-title{font-size:1.1rem;margin:.1rem 0 .75rem;word-break:break-word}
.ed-row{margin:0 0 .9rem}
.ed-row>label,.ed-label{display:block;font-weight:600;font-size:14px;margin-bottom:.25rem}
.ed-check{display:flex;align-items:center;gap:.5rem}
.ed-check input{width:20px;height:20px;margin:0;accent-color:#0052a5}
.ed-check label{margin:0}
#edPanel input[type=text],#edPanel textarea,#edPanel select{min-height:38px;padding:.4rem .6rem;font-size:15px}
#edPanel textarea{min-height:4.5rem}
.ed-hint{color:#4b5563;font-size:13px;margin:.3rem 0 0}
.ed-err{color:#b3261e;font-weight:600}
.ed-err:empty{display:none}
.ed-evidence{background:#f6f8fb;border-left:4px solid #8a94a6;border-radius:4px;padding:.5rem .75rem;margin:0 0 .75rem;font-size:14px}
.ed-evidence.paper{border-left-color:#1e7b34}
.ed-evidence p{margin:.15rem 0}
.ed-probs{margin:0 0 .75rem;padding:0;list-style:none}
.ed-probs:empty{display:none}
.ed-probs li{color:#8c1d18;background:#fdecea;border-radius:4px;padding:.4rem .6rem;margin-bottom:.35rem;font-size:14px}
.ed-link{background:none;border:0;padding:0;font:inherit;font-weight:700;color:#0052a5;text-decoration:underline;cursor:pointer;text-align:left}
.ed-note{background:#e8f0fb;border-left:4px solid #0052a5;border-radius:4px;padding:.45rem .7rem;margin:0 0 .9rem;font-size:14px}
.ed-kind-row{display:flex;align-items:center;justify-content:space-between;gap:.5rem;min-height:34px}
.ed-block{margin:0 0 .75rem}
.ed-opts{list-style:none;margin:0 0 .4rem;padding:0}
.ed-opts li{display:flex;gap:.3rem;margin-bottom:.35rem}
.ed-opts input{flex:1}
.ed-opts .ed-btn{min-width:34px;padding:.3rem .45rem}
.ed-consents{list-style:none;margin:0 0 .5rem;padding:0}
.ed-consents li{margin-bottom:.75rem}
.ed-consent-row{display:flex;justify-content:space-between;align-items:center;margin-top:.3rem}
.ed-cond-box{border-top:1px solid #e3e7ec;padding-top:.8rem}
.ed-cond-line{display:grid;grid-template-columns:1fr;gap:.2rem;margin-top:.5rem;font-size:14px}
.ed-actions{display:flex;flex-wrap:wrap;gap:.3rem;border-top:1px solid #e3e7ec;padding-top:.9rem;margin-top:.4rem}
.ed-actions .ed-btn{padding:.3rem .45rem}
[data-fid]{scroll-margin-top:64px}
.ed-bad{outline:2px solid #b3261e;outline-offset:4px;border-radius:6px}
.ed-sel{outline:3px solid #0052a5;outline-offset:4px;border-radius:6px}
body:not(.sidebar) .ed-sel:not(.ed-bad){outline:none}  /* collapsed: the selection is remembered, not shown */
body:not(.sidebar) .ed-sel.ed-bad{outline:2px solid #b3261e}
.ed-prob{color:#b3261e;font-weight:600;font-size:13px;margin:.3rem 0 0}
.card>.ed-prob{margin:-.6rem 0 1rem}
.ed-dot{color:#b3261e;font-size:12px;margin-left:.15rem}
body.ed-on .stepper li{display:flex;align-items:center}
@media (max-width:560px){  /* a phone-wide frame: the open sidebar becomes a sheet at the bottom */
  body.ed-on.sidebar{padding-right:0;padding-bottom:55vh}
  body.sidebar #edPanel{top:auto;left:0;width:auto;height:55vh;border-left:0;border-top:1px solid #c5ccd6}
}
`;
