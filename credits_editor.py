"""Page de personnalisation des crédits : éditeur sobre avec aperçu en direct (servie par le bot, protégée par l'adresse secrète)."""
from __future__ import annotations

import json
from typing import Any

import credits

TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crédits : personnalisation</title>
<style>
:root { --bg:#f3f3f6; --panel:#ffffff; --line:#e1e1e8; --text:#1b1b22; --muted:#6b6b7a; --accent:#5b45e0; --accent-text:#ffffff; --field:#efeff4; --ok:#1f8a4c; --warn:#b45309; color-scheme: light dark; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0e0e12; --panel:#16161c; --line:#2a2a34; --text:#ececf1; --muted:#9a9aa9; --accent:#8d7bff; --accent-text:#0e0e12; --field:#1f1f28; --ok:#4ade80; --warn:#fbbf24; }
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.app { display: grid; grid-template-rows: auto 1fr; height: 100vh; }
.bar { display: flex; align-items: center; gap: 12px; padding: 12px 20px; background: var(--panel); border-bottom: 1px solid var(--line); }
.bar h1 { margin: 0; font-size: 15px; font-weight: 650; letter-spacing: .01em; }
.bar .status { margin-left: auto; color: var(--muted); font-size: 13px; }
.bar .status.dirty { color: var(--warn); }
.bar .status.saved { color: var(--ok); }
button { font: inherit; color: var(--text); background: var(--field); border: 1px solid var(--line); border-radius: 8px; padding: 7px 13px; cursor: pointer; }
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: .5; cursor: default; }
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); font-weight: 600; }
.content { display: grid; grid-template-columns: minmax(330px, 410px) minmax(0, 1fr); min-height: 0; }
.side { overflow: auto; background: var(--panel); border-right: 1px solid var(--line); padding: 8px 0 40px; }
details { border-bottom: 1px solid var(--line); }
summary { cursor: pointer; list-style: none; padding: 13px 20px; font-weight: 600; display: flex; justify-content: space-between; }
summary::after { content: "+"; color: var(--muted); font-weight: 400; }
details[open] summary::after { content: "–"; }
summary::-webkit-details-marker { display: none; }
.group { padding: 2px 20px 16px; display: grid; gap: 12px; }
.row { display: grid; gap: 5px; }
.row[hidden] { display: none; }
.row > label { color: var(--muted); font-size: 12.5px; }
.row.inline { grid-template-columns: 1fr auto; align-items: center; }
.row.inline > label { color: var(--text); font-size: 14px; }
.ctl { display: flex; align-items: center; gap: 10px; min-width: 0; }
input[type=text], select { width: 100%; font: inherit; color: var(--text); background: var(--field); border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; }
input[type=color] { width: 44px; height: 32px; padding: 2px; background: var(--field); border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }
input[type=range] { flex: 1; accent-color: var(--accent); }
.val { min-width: 74px; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12.5px; }
code { color: var(--muted); font-size: 12.5px; }
.switch { position: relative; width: 38px; height: 22px; flex: none; }
.switch input { opacity: 0; position: absolute; inset: 0; margin: 0; cursor: pointer; z-index: 1; }
.switch span { position: absolute; inset: 0; background: var(--line); border-radius: 22px; transition: .15s; }
.switch span::after { content: ""; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px; background: #fff; border-radius: 50%; transition: .15s; }
.switch input:checked + span { background: var(--accent); }
.switch input:checked + span::after { transform: translateX(16px); }
.sec { display: grid; grid-template-columns: auto 1fr auto auto; gap: 8px; align-items: center; }
.sec button { padding: 4px 9px; }
.sec .name { grid-column: 2 / 5; color: var(--muted); font-size: 12px; margin-bottom: -4px; }
.presets { padding: 14px 20px; display: grid; gap: 10px; border-bottom: 1px solid var(--line); }
.presets .line { display: flex; gap: 8px; }
.presets .line > * { flex: 1; }
.presets .line button { flex: none; }
.main { display: flex; flex-direction: column; min-width: 0; padding: 18px 22px; gap: 12px; overflow: auto; }
.tools { display: flex; align-items: center; gap: 10px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { border: 0; border-radius: 0; background: var(--panel); }
.seg button.on { background: var(--accent); color: var(--accent-text); font-weight: 600; }
.hint { margin-left: auto; color: var(--muted); font-size: 12.5px; }
.stage { position: relative; width: 100%; border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
  background: repeating-conic-gradient(#8884 0% 25%, #0000 0% 50%) 0 0 / 22px 22px, var(--panel); }
.stage iframe { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; border: 0; transform-origin: 0 0; background: transparent; }
.note { color: var(--muted); font-size: 12.5px; }
.stage.blur { background: radial-gradient(60% 70% at 70% 30%, #e8a0ff 0%, transparent 60%), radial-gradient(70% 80% at 20% 80%, #ff3d5a66 0%, transparent 60%), linear-gradient(135deg, #0ea5e9, #1e3a8a); }
.stage.black { background: #000; }
.tools select { width: auto; }
@media (max-width: 900px) { .app { height: auto; } .content { grid-template-columns: 1fr; } .side { border-right: 0; } }
</style>
</head>
<body>
<div class="app">
  <div class="bar">
    <h1>Crédits : personnalisation</h1>
    <button id="reset" type="button">Réinitialiser</button>
    <button id="save" type="button" class="primary" disabled>Enregistrer</button>
    <span id="status" class="status"></span>
  </div>
  <div class="content">
    <aside class="side">
      <div class="presets">
        <div class="line"><select id="preset-select" aria-label="Préréglage"></select><button id="preset-apply" type="button">Appliquer</button></div>
        <div class="line"><input id="preset-name" type="text" maxlength="30" placeholder="Nom du nouveau préréglage"><button id="preset-save" type="button">Enregistrer</button></div>
        <div class="line"><button id="preset-delete" type="button" disabled>Supprimer le préréglage choisi</button></div>
      </div>
      <div id="form"></div>
    </aside>
    <main class="main">
      <div class="tools">
        <div class="seg" role="group" aria-label="Mode d'aperçu">
          <button id="mode-static" type="button" class="on">Aperçu fixe</button>
          <button id="mode-play" type="button">Lecture</button>
        </div>
        <button id="replay" type="button">Relancer</button>
        <select id="stage-bg" aria-label="Fond de l'aperçu"><option value="">Fond : damier</option><option value="blur">Fond : flou bleu</option><option value="black">Fond : noir</option></select>
        <span class="hint">Données fictives · 1920×1080</span>
      </div>
      <div class="stage" id="stage"><iframe id="frame" sandbox="allow-scripts" title="Aperçu des crédits"></iframe></div>
      <p class="note">« Enregistrer » applique ce style aux crédits affichés dans OBS. Recharge la source (ou réaffiche la scène de fin) pour le voir. Les polices viennent de ton ordinateur : l'aperçu est fidèle si tu l'ouvres sur la machine qui fait tourner OBS. La page est transparente : pour flouter l'image derrière, ajoute dans OBS un filtre « Flou gaussien » sur la caméra ou le jeu (la source placée sous les crédits).</p>
    </main>
  </div>
</div>
<script id="state" type="application/json">__STATE__</script>
<script>
(function () {
  var S = JSON.parse(document.getElementById('state').textContent);
  var style = clone(S.style), savedJson = JSON.stringify(S.style), userPresets = S.user_presets, mode = 'static', seq = 0, timer = null;
  var $ = function (id) { return document.getElementById(id); };

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function h(tag, attrs) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') e.textContent = attrs[k]; else if (k === 'class') e.className = attrs[k]; else e.setAttribute(k, attrs[k]); });
    for (var i = 2; i < arguments.length; i++) if (arguments[i]) e.appendChild(arguments[i]);
    return e;
  }
  function api(path, body) {
    return fetch(new URL(path, location.href), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }

  // ---- état et statut
  function isDirty() { return JSON.stringify(style) !== savedJson; }
  function refreshStatus(msg, cls) {
    var st = $('status');
    if (msg) { st.textContent = msg; st.className = 'status ' + (cls || ''); }
    else if (isDirty()) { st.textContent = 'Modifications non enregistrées'; st.className = 'status dirty'; }
    else { st.textContent = ''; st.className = 'status'; }
    $('save').disabled = !isDirty();
  }
  function changed() { refreshStatus(); updateVisibility(); schedulePreview(); }
  function set(key, value) { style[key] = value; changed(); }

  // ---- aperçu
  function fit() {
    var stage = $('stage'), k = stage.clientWidth / 1920;
    $('frame').style.transform = 'scale(' + k + ')';
    stage.style.height = (1080 * k) + 'px';
  }
  function schedulePreview() { clearTimeout(timer); timer = setTimeout(preview, 250); }
  function preview() {
    var mine = ++seq;
    api('render', { style: style, mode: mode }).then(function (r) { return r.text(); }).then(function (html) {
      if (mine === seq) $('frame').srcdoc = html;
    });
  }
  function setMode(m) {
    mode = m;
    $('mode-static').className = m === 'static' ? 'on' : '';
    $('mode-play').className = m === 'play' ? 'on' : '';
    preview();
  }

  // ---- formulaire
  function control(f) {
    var v = style[f.key], inp, out;
    if (f.type === 'color') {
      inp = h('input', { type: 'color' }); inp.value = v; out = h('code', { text: v });
      inp.addEventListener('input', function () { out.textContent = inp.value; set(f.key, inp.value); });
      return h('div', { class: 'ctl' }, inp, out);
    }
    if (f.type === 'range') {
      inp = h('input', { type: 'range', min: f.min, max: f.max, step: f.step }); inp.value = v;
      out = h('span', { class: 'val', text: v + ' ' + f.unit });
      inp.addEventListener('input', function () { var n = parseFloat(inp.value); out.textContent = n + ' ' + f.unit; set(f.key, n); });
      return h('div', { class: 'ctl' }, inp, out);
    }
    if (f.type === 'select') {
      inp = h('select');
      f.options.forEach(function (o) { var opt = h('option', { value: o[0], text: o[1] }); if (String(v) === o[0]) opt.selected = true; inp.appendChild(opt); });
      inp.addEventListener('change', function () { set(f.key, inp.value); });
      return h('div', { class: 'ctl' }, inp);
    }
    if (f.type === 'toggle') {
      inp = h('input', { type: 'checkbox' }); inp.checked = !!v;
      inp.addEventListener('change', function () { set(f.key, inp.checked); });
      return h('span', { class: 'switch' }, inp, h('span'));
    }
    inp = h('input', { type: 'text', maxlength: f.max }); inp.value = v;
    inp.addEventListener('input', function () { set(f.key, inp.value); });
    return h('div', { class: 'ctl' }, inp);
  }

  function sectionsUi() {
    var box = h('div', { class: 'row' });
    box.appendChild(h('label', { text: 'Rubriques : ordre, titre, affichage' }));
    style.sections.forEach(function (s, i) {
      var on = h('input', { type: 'checkbox' }); on.checked = s.on;
      on.addEventListener('change', function () { s.on = on.checked; changed(); });
      var title = h('input', { type: 'text', maxlength: 60 }); title.value = s.title;
      title.addEventListener('input', function () { s.title = title.value; changed(); });
      var up = h('button', { type: 'button', text: '↑', 'aria-label': 'Monter' }); up.disabled = i === 0;
      var down = h('button', { type: 'button', text: '↓', 'aria-label': 'Descendre' }); down.disabled = i === style.sections.length - 1;
      up.addEventListener('click', function () { move(i, -1); });
      down.addEventListener('click', function () { move(i, 1); });
      box.appendChild(h('div', { class: 'sec' }, h('span', { class: 'switch' }, on, h('span')), title, up, down));
      box.appendChild(h('div', { class: 'sec' }, h('span', { class: 'name', text: S.section_labels[s.id] })));
    });
    return box;
  }
  function move(i, d) {
    var a = style.sections, j = i + d; var t = a[i]; a[i] = a[j]; a[j] = t;
    buildForm(); changed();
  }

  var openGroups = { 'Modèle': true, 'Couleurs': true };
  function buildForm() {
    var root = $('form'); root.textContent = '';
    var groups = [];
    S.fields.forEach(function (f) { if (groups.indexOf(f.group) < 0) groups.push(f.group); });
    groups.forEach(function (g) {
      var det = h('details'); if (openGroups[g]) det.open = true;
      det.addEventListener('toggle', function () { openGroups[g] = det.open; });
      det.appendChild(h('summary', { text: g }));
      var body = h('div', { class: 'group' });
      if (g === 'Contenu') body.appendChild(sectionsUi());
      S.fields.filter(function (f) { return f.group === g; }).forEach(function (f) {
        var row = h('div', { class: 'row' + (f.type === 'toggle' ? ' inline' : '') });
        row.appendChild(h('label', { text: f.label }));
        row.appendChild(control(f));
        row._show = f.show || null;
        body.appendChild(row);
      });
      det.appendChild(body); root.appendChild(det);
    });
    updateVisibility();
  }
  function updateVisibility() {
    document.querySelectorAll('#form .row').forEach(function (row) {
      if (!row._show) return;
      var ok = Object.keys(row._show).every(function (k) { return row._show[k].indexOf(style[k]) >= 0; });
      row.hidden = !ok;
    });
  }

  // ---- préréglages
  function refreshPresets(selected) {
    var sel = $('preset-select'); sel.textContent = '';
    var og1 = h('optgroup', { label: 'Préréglages' }), og2 = h('optgroup', { label: 'Mes préréglages' });
    Object.keys(S.builtin_presets).forEach(function (n) { og1.appendChild(h('option', { value: 'b:' + n, text: n })); });
    Object.keys(userPresets).forEach(function (n) { og2.appendChild(h('option', { value: 'u:' + n, text: n })); });
    sel.appendChild(og1); if (og2.children.length) sel.appendChild(og2);
    if (selected) sel.value = selected;
    $('preset-delete').disabled = sel.value.indexOf('u:') !== 0;
  }
  function chosen() {
    var v = $('preset-select').value, name = v.slice(2);
    return { user: v.indexOf('u:') === 0, name: name, style: v.indexOf('u:') === 0 ? userPresets[name] : S.builtin_presets[name] };
  }
  $('preset-select').addEventListener('change', function () { $('preset-delete').disabled = chosen().user === false; });
  $('preset-apply').addEventListener('click', function () { style = clone(chosen().style); buildForm(); changed(); });
  $('preset-save').addEventListener('click', function () {
    var name = $('preset-name').value.trim();
    if (!name) { refreshStatus('Donne un nom au préréglage.', 'dirty'); return; }
    api('presets', { name: name, style: style }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); }).then(function (res) {
      if (!res.ok) { refreshStatus(res.j.error || 'Impossible d\'enregistrer le préréglage.', 'dirty'); return; }
      userPresets = res.j.presets; refreshPresets('u:' + name); $('preset-name').value = '';
      refreshStatus('Préréglage « ' + name + ' » enregistré.', 'saved');
    });
  });
  $('preset-delete').addEventListener('click', function () {
    var c = chosen(); if (!c.user) return;
    api('presets/delete', { name: c.name }).then(function (r) { return r.json(); }).then(function (j) {
      userPresets = j.presets; refreshPresets(); refreshStatus('Préréglage supprimé.', 'saved');
    });
  });

  // ---- actions
  $('save').addEventListener('click', function () {
    $('save').disabled = true;
    api('style', { style: style }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); }).then(function (res) {
      if (!res.ok) { refreshStatus(res.j.error || 'Enregistrement impossible.', 'dirty'); $('save').disabled = false; return; }
      style = res.j.style; savedJson = JSON.stringify(style); buildForm(); refreshStatus('Enregistré ✓', 'saved'); schedulePreview();
    }).catch(function () { refreshStatus('Enregistrement impossible (connexion).', 'dirty'); $('save').disabled = false; });
  });
  $('reset').addEventListener('click', function () { style = clone(S.defaults); buildForm(); changed(); });
  $('replay').addEventListener('click', preview);
  $('stage-bg').addEventListener('change', function () { $('stage').className = 'stage ' + $('stage-bg').value; });
  $('mode-static').addEventListener('click', function () { setMode('static'); });
  $('mode-play').addEventListener('click', function () { setMode('play'); });
  window.addEventListener('beforeunload', function (e) { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('resize', fit);

  buildForm(); refreshPresets(); refreshStatus(); fit(); preview();
})();
</script>
</body>
</html>
"""


def render_editor(style: dict[str, Any], user_presets: dict[str, dict[str, Any]]) -> str:
    state = {
        "fields": credits.FIELDS,
        "style": style,
        "defaults": credits.default_style(),
        "builtin_presets": {name: credits.preset_style(name) for name in credits.PRESETS},
        "user_presets": user_presets,
        "section_labels": credits.SECTION_LABELS,
    }
    payload = json.dumps(state, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return TEMPLATE.replace("__STATE__", payload)
