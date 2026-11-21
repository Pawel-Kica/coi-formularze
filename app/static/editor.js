"use strict";
/* Click editor: the edit mode of /preview?embed=1, driven by the upload page (index.html).
   The clerk clicks an element of the e-service and fixes it in the side panel; every change is
   checked by the server with the real COI rules (POST /api/forms/check, called by the parent).

   Protocol (same-origin postMessage):
   - here -> parent {type: "coi-edit-request", id}: "Edytuj" was clicked next to an element of the read-only preview
     (preview.html sends it); the parent switches to "Edytuj" and answers with coi-preview {edit: true, select: id}.
   - parent -> here {type: "coi-preview", json, edit: true, evidence, doc, key, select?}: preview.html loads the JSON and its
     evidence as always, then this file switches to edit mode. Without `edit` the preview stays read-only. `doc`
     numbers the document; every message back carries it, so the parent ignores edits of a document it no longer shows.
   - here -> parent {type: "coi-edit", json, evidence, version} after every change, undo and redo;
     the parent saves it in the history entry and sends back
     {type: "coi-problems", problems: [{id, message, detail}], version, doc}.
   - parent -> here {type: "coi-undo"} / {type: "coi-redo"}: Ctrl+Z pressed while the parent had focus.

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
  ["TERYT_4G_DOWODOWY", "Słownik COI: TERYT (województwo, powiat, gmina, miejscowość)"]];
const ED_DICT_EXTRA = {
  Kraje: {multiLevelTree: false, searchEnabled: true, defaultValue: ["Polska"]},
  TERYT_4G_DOWODOWY: {multiLevelTree: true, searchEnabled: true, defaultValue: []},
};
const ED_EMPTY_HTML = "<html><head></head><body></body></html>";

const ed = {on: false, doc: 0, key: undefined, sel: null, version: 0, checked: -1, problems: [], undo: [], redo: [],
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

// Standard pages (Dane, Kontakt i zgody, finish) come from the COI Standards and stay as code built them.
const edEditable = loc => loc && (loc.kind === "title" || !isStandard(loc.tab));
// On standard pages only what came from the paper is editable: the addressee and the statements (consents).
const edAddressee = loc => loc.kind === "field" && ADDRESSEE_FIELDS.includes(loc.el.techName);
const edConsents = loc => loc.kind === "field" && loc.el.type === "CONSENT_SECTION" && Array.isArray(loc.el.consentSectionConfig?.consents);

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
function edChange(fn, note = "") {
  edFlush();
  ed.undo.push(edSnapshot());
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
  if (loc && title) title.textContent = (loc.kind === "title" ? cfg.title : loc.kind === "tab" ? loc.tab.title : labelOf(loc.el)) || "(bez tytułu)";
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
  ed.note = "Cofnięto ostatnią zmianę.";
  edAfter();
}
function edRedo() {
  edFlush();
  const s = ed.redo.pop();
  if (!s) return;
  ed.undo.push(edSnapshot());
  edRestore(s);
  ed.note = "Przywrócono zmianę.";
  edAfter();
}
function edPost() {
  parent.postMessage({type: "coi-edit", json: JSON.stringify(cfg, null, 2), evidence: evidence, version: ed.version, doc: ed.doc}, location.origin);
}
// Re-reads the whole config after a change (answers are keyed by titles and techNames) and redraws the page.
function edRerender() {
  issues = [];
  conds.clear();
  normalize(cfg);
  pages = orderPages();
  precheck();
  resetAnswers();
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

function edNewField(title = "Nowe pole") {
  return {id: edNewId(), type: "TEXT_FIELD", techName: edTech(title), title, description: "", options: [], validation: [],
    readOnly: false, repeatingSectionGrid: [], states: [], questionOrder: 0, tooltipMessage: ""};
}
function edNewSection(title = "Nowa sekcja") {
  const sec = {id: edNewId(), type: "SECTION", techName: edTech(title), title, description: ED_EMPTY_HTML, options: [],
    validation: [], readOnly: false, repeatingSectionGrid: [], states: [], questionOrder: 0, tooltipMessage: ""};
  const f = edNewField();
  f.techName = edTech("Nowe pole", sec.id);
  sec.repeatingSectionGrid.push({x: 0, y: 0, rows: 1, cols: 3, field: f});
  return sec;
}
const edMark = (...els) => { for (const el of els) evidence[el.id] = {source: "clerk", quote: null, page: null}; };

// Adds a new text field to section `secId`, after field `afterId` (or at the end).
function edAddField(secId, afterId) {
  edChange(() => {
    const loc = edFind(secId), sec = loc.el, f = edNewField();
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
}
// Adds a new section (with one field) to tab `tabId`, after section `afterId` (or at the end).
function edAddSection(tabId, afterId) {
  edChange(() => {
    const tab = edFind(tabId).el, sec = edNewSection();
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
function edRemove(id) {
  const loc = edFind(id);
  const gone = loc.kind === "section" ? [loc.el, ...(loc.el.repeatingSectionGrid || []).map(c => c.field)] : [loc.el];
  const deps = edDependents(gone).length;
  const what = loc.kind === "section" ? "sekcję" : "pole";
  edChange(() => {
    if (loc.kind === "field") {
      loc.section.repeatingSectionGrid = loc.section.repeatingSectionGrid.filter(c => c.field.id !== id);
      edLayoutSection(loc.section);
    } else loc.tab.questions = loc.tab.questions.filter(q => q.field.id !== id);
    edLayoutTab(loc.tab);
    ed.sel = loc.kind === "field" ? loc.section.id : null;
  }, `Usunięto ${what}. ` + (deps
    ? `Od usuniętego zależy warunek: ${deps} ${plural(deps, ["element", "elementy", "elementów"])}. Popraw te warunki (czerwone) albo cofnij (Ctrl+Z).`
    : "Cofniesz to przez Ctrl+Z."));
}

// Type change: keeps label, description and REQUIRED; drops what the new type cannot have.
function edSetType(f, type) {
  const required = validators(f).includes("REQUIRED") && type !== "TEXT_BLOCK";
  const keepFormat = type === "TEXT_FIELD" ? validators(f).filter(v => v !== "REQUIRED") : [];
  f.type = type;
  f.validation = [...(required ? ["REQUIRED"] : []), ...keepFormat];
  if (!ED_CHOICE.includes(type)) f.options = [];
  if (type !== "SELECT" || !f.dictionaryName) for (const k of ["dictionaryName", "multiLevelTree"]) delete f[k];
  for (const k of ["searchEnabled", "defaultValue", "defaultValueType"]) delete f[k];
  if (type === "SELECT") Object.assign(f, f.dictionaryName ? ED_DICT_EXTRA[f.dictionaryName] || {} : {searchEnabled: f.options.length > 15, defaultValue: []});
  edCanonical(f);
}
function edSetDictionary(f, name) {
  for (const k of ["dictionaryName", "multiLevelTree", "searchEnabled", "defaultValue", "defaultValueType"]) delete f[k];
  if (name) { f.options = []; Object.assign(f, {dictionaryName: name}, ED_DICT_EXTRA[name] || {}); }
  else Object.assign(f, {searchEnabled: f.options.length > 15, defaultValue: []});
  edCanonical(f);
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

/* ---------- the form in edit mode ---------- */
const edProblemsOf = id => ed.problems.filter(p => p.id === id);

// After every render in edit mode: clickable elements, problems, "add" buttons. The paper quotes and
// conditions under each element are drawn by preview.html itself (notesOf), the same as in the preview.
function edDecorate() {
  const tab = pages[pageIdx], standard = isStandard(tab), page = $("#page");
  const head = $("#pageHeading"), title = $("#formTitle");
  head.dataset.fid = tab.id;
  title.dataset.fid = "__title";
  for (const el of [title, ...page.querySelectorAll("[data-fid]")]) {
    const id = el.dataset.fid;
    el.classList.add("ed-item");
    el.tabIndex = 0;
    el.classList.toggle("ed-sel", id === ed.sel);
    el.classList.toggle("ed-bad", edProblemsOf(id).length > 0);
    const probs = edProblemsOf(id).map(p => h("p", {class: "ed-prob"}, "⚠ " + p.message));
    if (!probs.length || id === "__title") continue;
    if (el === head) el.after(...probs);
    else if (el.matches("section.card")) ($(":scope > h3", el) || el.firstChild).after(...probs);
    else el.append(...probs);
  }
  if (!standard) {
    for (const card of page.querySelectorAll("section.card[data-fid]"))
      card.append(h("button", {type: "button", class: "ed-add", onclick: () => edAddField(card.dataset.fid, null)}, "+ Dodaj pole w tej sekcji"));
    $("#page > .grid")?.after(h("button", {type: "button", class: "ed-add ed-add-section", onclick: () => edAddSection(tab.id, null)}, "+ Dodaj sekcję na tej stronie"));
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
    h("div", {class: "ed-top"},
      h("div", {class: "ed-head"}, h("h2", {}, "Edycja"),
        h("button", {type: "button", class: "ed-btn", id: "edUndo", title: "Cofnij (Ctrl+Z)", onclick: edUndo}, "Cofnij"),
        h("button", {type: "button", class: "ed-btn", id: "edRedo", title: "Ponów (Ctrl+Shift+Z)", onclick: edRedo}, "Ponów")),
      h("div", {id: "edStatus", role: "status"})),
    h("div", {id: "edBody"})));
}

// Status line, undo/redo buttons and the problem lists; never touches the inputs (focus stays).
function edStatus() {
  const n = ed.problems.length, box = $("#edStatus");
  const waiting = ed.checked !== ed.version;
  box.className = "ed-status " + (waiting ? "wait" : n ? "bad" : "ok");
  // always says that nothing has to be saved by hand; with problems and an element open, one click shows the whole list
  box.replaceChildren(waiting ? "Sprawdzam zgodność z regułami COI…"
    : n ? `Do poprawy: ${n} ${plural(n, ["problem", "problemy", "problemów"])}. Pobieranie JSON-a jest zablokowane. `
    : "Zmiany zapisują się same. Zgodne z regułami COI, możesz pobrać JSON.",
    !waiting && n && ed.sel ? h("button", {type: "button", class: "ed-link", onclick: () => edSelect(null)}, "Pokaż listę") : "");
  $("#edUndo").disabled = !ed.undo.length && !ed.pending;
  $("#edRedo").disabled = !ed.redo.length;
  const mine = $("#edMine");
  if (mine) mine.replaceChildren(...edProblemsOf(ed.sel).map(p => h("li", {title: p.detail}, p.message)));
  const all = $("#edAll");
  if (all) {
    all.replaceChildren(...ed.problems.map(p => {
      const loc = p.id && edFind(p.id);
      const where = !loc ? "Cały formularz" : loc.kind === "title" ? "Tytuł e-usługi" : loc.kind === "tab" ? `Strona „${loc.tab.title}”`
        : `${loc.tab.title} › ${labelOf(loc.el)}`;
      return h("li", {title: p.detail}, loc ? h("button", {type: "button", class: "ed-link", onclick: () => edReveal(p.id)}, where) : h("strong", {}, where),
        h("br"), p.message);
    }));
    $("#edAllWrap").hidden = !ed.problems.length;
  }
}

// Shows an element: its page, selection, scroll.
function edReveal(id) {
  const loc = edFind(id);
  if (!loc) return;
  if (loc.tab && pages.indexOf(loc.tab) !== pageIdx) go(pages.indexOf(loc.tab), false);
  edSelect(id);
  document.querySelector(`[data-fid="${CSS.escape(id)}"]`)?.scrollIntoView({block: "center"});
}
function edSelect(id) {
  edFlush();
  ed.sel = id;
  ed.note = "";
  for (const el of document.querySelectorAll(".ed-sel")) el.classList.remove("ed-sel");
  if (id) document.querySelector(`[data-fid="${CSS.escape(id)}"]`)?.classList.add("ed-sel");
  edPanel();
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
  if (note) $("#edPanel").scrollTop = 0;  // the note says what just happened (e.g. after "Usuń" at the bottom of the panel)
}

function edHelpPanel() {
  return [
    h("p", {class: "ed-lead"}, "Kliknij dowolny element formularza, aby go poprawić."),
    h("ul", {class: "ed-tips"},
      h("li", {}, "Przy każdym polu widać cytat z papieru, z którego powstało."),
      h("li", {}, "Ctrl+Z cofa ostatnią zmianę."),
      h("li", {}, "Po każdej zmianie sprawdzamy formularz regułami COI. Problemy są zaznaczone na czerwono.")),
    h("div", {id: "edAllWrap", hidden: true}, h("h3", {class: "ed-h3"}, "Do poprawy"), h("ol", {class: "ed-probs", id: "edAll"})),
  ];
}

function edEvidenceBox(loc) {
  if (loc.kind === "title") return null;
  const ev = evidence[loc.el.id], box = h("div", {class: "ed-evidence"});
  const quote = (label, text) => h("p", {}, h("strong", {}, label + ": "), `„${text}”`);
  if (!ev) box.append(h("p", {}, "Brak informacji o pochodzeniu (wynik bez cytatów: starszy albo wczytany z pliku)."));
  else if (ev.source === "paper" && ev.quote) {
    box.classList.add("paper");
    box.append(quote("Z papieru", ev.quote));
  } else {
    box.append(h("p", {}, {
      model: loc.kind === "tab" ? "Stronę ułożył model, grupując sekcje. Strona nie ma własnego cytatu."
        : "Sekcję ułożył model, grupując pola z papieru. Sekcja nie ma własnego cytatu, cytaty są przy jej polach.",
      code: "Dodane przez kod ze Standardów COI. Tego elementu nie ma na papierze.",
      clerk: "Dodane przez Ciebie w edytorze. Nie ma cytatu z papieru.",
      paper: "Z papieru, bez zapisanego cytatu.",
    }[ev.source] || "Brak informacji o pochodzeniu."));
  }
  if (ev?.condition_quote) box.append(quote("Warunek z papieru", ev.condition_quote));
  if (ev?.condition_source === "clerk") box.append(h("p", {}, "Warunek ustawiony przez Ciebie."));
  (ev?.consent_quotes || []).forEach((q, i) => box.append(q ? quote(`Oświadczenie ${i + 1} z papieru`, q)
    : h("p", {}, h("strong", {}, `Oświadczenie ${i + 1}: `), i === 0 ? "standard COI (klauzula RODO), dodana przez kod" : "dodane przez Ciebie, bez cytatu")));
  return box;
}

function edElementPanel(loc) {
  const kind = {title: "Tytuł e-usługi", tab: "Strona", section: ED_TYPE_NAMES[loc.el?.type] || "Sekcja", field: "Pole"}[loc.kind];
  const name = loc.kind === "title" ? cfg.title : loc.kind === "tab" ? loc.tab.title : labelOf(loc.el);
  const out = [h("p", {class: "ed-kind"}, kind + (loc.kind === "field" ? ` · ${ED_TYPE_NAMES[loc.el.type] || loc.el.type}` : "")),
    h("h3", {class: "ed-title"}, name || "(bez tytułu)"), edEvidenceBox(loc) || "", h("ul", {class: "ed-probs", id: "edMine"})];
  if (!edEditable(loc)) {
    if (edAddressee(loc)) out.push(...edAddresseeControls(loc.el));
    else if (edConsents(loc)) out.push(...edConsentControls(loc.el));
    else out.push(h("p", {class: "ed-readonly"}, "Tę stronę buduje kod według Standardów COI, taka sama w każdej e-usłudze. " +
      "Poprawisz na takich stronach tylko to, co jest z papieru: adresata wniosku i oświadczenia."));
    return out;
  }
  if (loc.kind === "title") out.push(edRow("Tytuł e-usługi", edInput(cfg.title || "", "label", v => { cfg.title = v; }),
    "Zaczyna się od „Wniosek”, „Zgłoszenie”, „Deklaracja” albo „Oświadczenie”."));
  else if (loc.kind === "tab") out.push(...edTabControls(loc));
  else if (loc.kind === "section") out.push(...edSectionControls(loc));
  else out.push(...edFieldControls(loc));
  return out;
}

function edAddresseeControls(f) {
  return [edRow("Adresat wniosku (urząd lub organ)", edInput(String(f.defaultValue ?? ""), "label", v => { f.defaultValue = v; }),
    "Obywatel widzi go jako stałą wartość. Puste: urząd uzupełni adresata sam.")];
}

// Statements from the paper ("Oświadczam, że ...") in the consent section; the first consent is the RODO clause (code).
function edConsentControls(f) {
  const list = f.consentSectionConfig.consents, quotes = () => evidence[f.id]?.consent_quotes;
  const items = list.map((c, i) => {
    if (i === 0) return h("li", {}, h("p", {class: "ed-hint"}, "1. Klauzula RODO: standard COI, bez zmian."));
    const cb = h("input", {type: "checkbox", "data-k": `consentreq${i}`, id: "ed" + (++edUid)});
    cb.checked = c.required === true;
    cb.addEventListener("change", () => edChange(() => { c.required = cb.checked; }));
    const ta = edArea(edText(c.description), `consent${i}`, v => { c.description = edHtml(v, ""); });
    ta.setAttribute("aria-label", `Treść oświadczenia ${i + 1}`);
    return h("li", {}, h("span", {class: "ed-label"}, `${i + 1}. Oświadczenie`), ta,
      h("div", {class: "ed-consent-row"}, h("span", {class: "ed-check"}, cb, h("label", {for: cb.id}, "Wymagane")),
        edBtn("Usuń", `consentdel${i}`, () => edChange(() => { list.splice(i, 1); quotes()?.splice(i, 1); }), {danger: true, label: `Usuń oświadczenie ${i + 1}`})));
  });
  const add = edBtn("+ Dodaj oświadczenie", "consentadd", () => edChange(() => {
    list.push({description: edHtml("Oświadczam, że ", ""), required: true});
    quotes()?.push(null);
  }));
  return [h("div", {class: "ed-row"}, h("span", {class: "ed-label"}, "Zgody i oświadczenia z papieru"), h("ol", {class: "ed-consents"}, items), add)];
}

function edTabControls(loc) {
  const tab = loc.tab, msg = h("p", {class: "ed-hint ed-err", role: "alert"});
  const inp = edInput(tab.title, "label", null, el => {
    const v = el.value.trim();
    msg.textContent = !v ? "Tytuł strony nie może być pusty." : cfg.tabs.some(t => t !== tab && t.title === v) ? "Taki tytuł ma już inna strona." : "";
    if (msg.textContent) { el.value = tab.title; return; }
    if (v !== tab.title) edChange(() => edRenameTab(tab, v));
  });
  return [edRow("Tytuł strony", inp, "Zmiana zapisuje się po Enter albo po wyjściu z pola; warunki wskazujące tę stronę zmienią się same."), msg,
    h("div", {class: "ed-actions"}, edBtn("+ Dodaj sekcję na końcu strony", "addsec", () => edAddSection(tab.id, null)))];
}

function edSectionControls(loc) {
  const s = loc.el, list = sortCells(loc.tab.questions), i = list.findIndex(q => q.field === s);
  const out = [
    edRow("Tytuł sekcji", edInput(s.title || "", "label", v => { s.title = v; edRetech(s); })),
    edRow("Opis pod tytułem", edArea(edText(s.description), "desc", v => { s.description = edHtml(v, ED_EMPTY_HTML); }), "Krótko i prostym językiem, opcjonalnie."),
  ];
  if (s.type === "REPEATING_SECTION") out.push(h("p", {class: "ed-hint"}, "Sekcja powtarzalna: obywatel może dodać kolejne pozycje (np. kolejnego psa)."));
  out.push(edConditionControls(loc), h("div", {class: "ed-actions"},
    edBtn("↑ W górę", "up", () => edMove(s.id, -1), {disabled: i <= 0, label: "Przesuń sekcję w górę"}),
    edBtn("↓ W dół", "down", () => edMove(s.id, 1), {disabled: i >= list.length - 1, label: "Przesuń sekcję w dół"}),
    edBtn("+ Pole na końcu", "addfield", () => edAddField(s.id, null)),
    edBtn("+ Sekcja poniżej", "addsec", () => edAddSection(loc.tab.id, s.id)),
    edBtn("Usuń sekcję", "del", () => edRemove(s.id), {danger: true})));
  return out;
}

function edFieldControls(loc) {
  const f = loc.el, t = f.type, list = sortCells(loc.section.repeatingSectionGrid), i = list.findIndex(c => c.field === f);
  const vs = validators(f), required = vs.includes("REQUIRED"), format = vs.find(v => v !== "REQUIRED") || "";
  const types = ED_TYPES.some(([v]) => v === t) ? ED_TYPES : [...ED_TYPES, [t, ED_TYPE_NAMES[t] || t]];
  const out = [
    edRow(t === "TEXT_BLOCK" ? "Nagłówek (opcjonalny)" : "Etykieta", edInput(f.title || "", "label", v => { f.title = v; edRetech(f); })),
    edRow("Typ", edSelectBox(types.map(([v, l]) => [v, l]), t, "type", v => edChange(() => edSetType(f, v),
      ED_CHOICE.includes(v) && !ED_CHOICE.includes(t) ? "Zmieniono typ. Dodaj opcje do wyboru." : ""))),
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
  if (t === "SELECT") {
    const dicts = ED_DICTS.some(([v]) => v === (f.dictionaryName || "")) ? ED_DICTS : [...ED_DICTS, [f.dictionaryName, `Słownik: ${f.dictionaryName}`]];
    out.push(edRow("Skąd lista", edSelectBox(dicts, f.dictionaryName || "", "dict", v => edChange(() => edSetDictionary(f, v)))));
  }
  if (ED_CHOICE.includes(t) && !f.dictionaryName) out.push(edOptionsControls(f));
  out.push(edRow(t === "TEXT_BLOCK" ? "Treść" : "Podpowiedź pod etykietą", edArea(edText(f.description), "desc", v => { f.description = edHtml(v, ""); }),
    t === "TEXT_BLOCK" ? null : "Opcjonalna, prostym językiem."));
  out.push(edConditionControls(loc), h("div", {class: "ed-actions"},
    edBtn("↑ W górę", "up", () => edMove(f.id, -1), {disabled: i <= 0, label: "Przesuń pole w górę"}),
    edBtn("↓ W dół", "down", () => edMove(f.id, 1), {disabled: i >= list.length - 1, label: "Przesuń pole w dół"}),
    edBtn("+ Pole poniżej", "addfield", () => edAddField(loc.section.id, f.id)),
    edBtn("Usuń pole", "del", () => edRemove(f.id), {danger: true})));
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
  if (text && !shown) box.append(h("p", {class: "ed-hint"}, `Teraz: widoczne, gdy ${text}. Wybór poniżej zastąpi ten warunek.`));
  const groups = new Map();
  for (const t of triggers) {
    if (!groups.has(t.tab)) groups.set(t.tab, []);
    groups.get(t.tab).push(t);
  }
  const fieldSel = h("select", {"data-k": "condfield", "aria-label": "Zależy od pola"}, h("option", {value: ""}, "nie zależy, zawsze widoczne"),
    [...groups].map(([tab, list]) => h("optgroup", {label: tab.title}, list.map(t => h("option", {value: t.f.id}, `${labelOf(t.f)} (${t.section.title || t.section.techName})`)))));
  fieldSel.value = shown ? single.field.id : "";
  fieldSel.addEventListener("change", () => {
    const trig = triggers.find(t => t.f.id === fieldSel.value)?.f;
    edChange(() => edSetCondition(target, trig || null, trig ? optionsOf(trig)[0] : null),
      trig ? "Ustawiono warunek. Wybierz opcję, przy której element ma się pokazać." : "Usunięto warunek: element jest zawsze widoczny.");
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

/* ---------- entering and leaving edit mode ---------- */
// `select` = the element to open right away ("Edytuj" next to it in the preview): shown, selected, its label ready
// to type in. `key` names the file: coming back to the same file (a look at "Podgląd" in between) keeps undo and redo.
function edEnter(doc, select, key) {
  if (!cfg || $("#app").hidden) return;  // the JSON did not load: preview shows the error
  if (key === undefined || key !== ed.key) Object.assign(ed, {undo: [], redo: []});
  Object.assign(ed, {on: true, doc, key, sel: null, version: 0, checked: -1, problems: [], pending: null, note: ""});
  edEnsurePanel();
  document.body.classList.add("editmode");
  applyModes();
  edRerender();
  edPanel();
  if (select && edFind(select)) { edReveal(select); edFocusLabel(); }
}
const edFocusLabel = () => $("[data-k=label]", $("#edBody"))?.focus({preventScroll: true});
function edLeave() {
  edFlush();
  ed.on = false;
  document.body.classList.remove("editmode");
  applyModes();
  if (cfg && !$("#app").hidden) renderPage();
}

{
  // Wrap the preview's render instead of editing it: decorations after each render.
  const baseRender = renderPage;
  renderPage = () => { baseRender(); if (ed.on) edDecorate(); };
}

window.addEventListener("message", e => {
  if (e.origin !== location.origin) return;
  const d = e.data || {};
  if (d.type === "coi-preview") { if (d.edit) edEnter(d.doc, d.select, d.key); else if (ed.on) edLeave(); }
  if (!ed.on) return;
  if (d.type === "coi-problems" && d.doc === ed.doc && d.version === ed.version) {
    // rules name no element for the form title: pin those on the title (h1)
    ed.problems = (Array.isArray(d.problems) ? d.problems : []).map(p => (!p.id && /^title /.test(p.detail || "") ? {...p, id: "__title"} : p));
    ed.checked = d.version;
    renderPage();
    edStatus();
  }
  if (d.type === "coi-undo") edUndo();
  if (d.type === "coi-redo") edRedo();
});

// Click (or Enter on a focused element) selects it; clicks never reach the form's own controls.
document.addEventListener("click", e => {
  if (!ed.on || e.target.closest(".ed-add, .nav, #stepNav, #edPanel")) return;
  const el = e.target.closest("[data-fid]");
  if (!el || !$("#app").contains(el)) return;
  e.preventDefault();
  edSelect(el.dataset.fid);
  edFocusLabel(); // click and type: the label is the most common fix
}, true);
document.addEventListener("keydown", e => {
  if (!ed.on) return;
  const inText = e.target.closest?.("input, textarea, select");
  const key = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && !inText && (key === "z" || key === "y")) {
    e.preventDefault();
    if (key === "y" || e.shiftKey) edRedo(); else edUndo();
  } else if ((e.key === "Enter" || e.key === " ") && e.target.classList?.contains("ed-item")) {
    e.preventDefault();
    edSelect(e.target.dataset.fid);
  } else if (e.key === "Escape" && !inText && ed.sel) edSelect(null);
});

const ED_CSS = `
body.editmode{padding-right:340px}
#edPanel{display:none;position:fixed;top:0;right:0;bottom:0;width:340px;background:#fff;border-left:1px solid #c5ccd6;box-shadow:-4px 0 16px rgba(16,24,40,.06);overflow:auto;font-size:15px;z-index:20}
body.editmode #edPanel{display:block}
.ed-top{position:sticky;top:0;z-index:1;background:#fff}
.ed-head{display:flex;align-items:center;gap:.5rem;padding:.6rem 1rem;border-bottom:1px solid #e3e7ec}
.ed-head h2{font-size:1.05rem;margin:0 auto 0 0}
.ed-btn{background:#fff;color:#003a80;border:1px solid #003a80;border-radius:4px;padding:.3rem .65rem;font:inherit;font-size:14px;cursor:pointer;min-height:34px}
.ed-btn:hover{background:#e8f0fb}
.ed-btn:disabled{opacity:.4;cursor:default;background:#fff}
.ed-btn.danger{color:#b3261e;border-color:#b3261e}
.ed-btn.danger:hover{background:#fdecea}
.ed-status{padding:.5rem 1rem;font-size:14px;font-weight:600;border-bottom:1px solid #e3e7ec}
.ed-status.ok{background:#e6f4ea;color:#14532d}
.ed-status.bad{background:#fdecea;color:#8c1d18}
.ed-status.wait{background:#f3f4f6;color:#4b5563}
#edBody{padding:1rem}
.ed-lead{font-weight:600;margin:0 0 .5rem}
.ed-tips{margin:0 0 1rem;padding-left:1.1rem;color:#4b5563;font-size:14px}
.ed-h3{font-size:1rem;margin:1rem 0 .5rem}
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
.ed-readonly{background:#fff7e0;border-left:4px solid #b7791f;border-radius:4px;padding:.5rem .75rem;font-size:14px}
.ed-opts{list-style:none;margin:0 0 .4rem;padding:0}
.ed-opts li{display:flex;gap:.3rem;margin-bottom:.35rem}
.ed-opts input{flex:1}
.ed-opts .ed-btn{min-width:34px;padding:.3rem .45rem}
.ed-consents{list-style:none;margin:0 0 .5rem;padding:0}
.ed-consents li{margin-bottom:.75rem}
.ed-consent-row{display:flex;justify-content:space-between;align-items:center;margin-top:.3rem}
.ed-cond-box{border-top:1px solid #e3e7ec;padding-top:.8rem}
.ed-cond-line{display:grid;grid-template-columns:5.5rem 1fr;align-items:center;gap:.5rem;margin-top:.4rem;font-size:14px}
.ed-actions{display:flex;flex-wrap:wrap;gap:.4rem;border-top:1px solid #e3e7ec;padding-top:.9rem;margin-top:.4rem}
body.editmode .cond-hidden{display:block!important}
body.editmode #page .grid :is(input,select,textarea,label,button:not(.ed-add)){pointer-events:none}
body.editmode .send{display:none}
body.editmode .tech{display:none}
.ed-item{cursor:pointer;border-radius:6px;outline:2px solid transparent;outline-offset:4px}
.ed-item:hover{outline-color:#9cb8dc}
.ed-item.ed-bad{outline:2px solid #b3261e}
.ed-item.ed-sel{outline:3px solid #0052a5}
.ed-prob{color:#b3261e;font-weight:600;font-size:13px;margin:.3rem 0 0}
.ed-add{display:block;width:100%;margin-top:.75rem;background:none;border:1px dashed #8a94a6;border-radius:6px;color:#0052a5;font:inherit;font-size:14px;font-weight:600;padding:.45rem .9rem;cursor:pointer}
.ed-add:hover{border-color:#0052a5;background:#f3f7fc}
.ed-add-section{margin-top:1rem;background:#fff}
.ed-dot{color:#b3261e;font-size:12px;margin-left:.15rem}
body.editmode .stepper li{display:flex;align-items:center}
@media (max-width:760px){
  body.editmode{padding-right:0;padding-bottom:55vh}
  #edPanel{top:auto;left:0;width:auto;height:55vh;border-left:0;border-top:1px solid #c5ccd6}
}
`;
