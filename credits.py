"""Crédits de fin de live : regroupe les données d'un stream et génère une page HTML pour OBS (source navigateur).

Tous les réglages de style passent par `normalize_style` : seules des valeurs validées (couleurs hexadécimales,
nombres bornés, choix dans une liste) arrivent dans le CSS, et tous les textes sont échappés dans le HTML.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
ANONYMOUS = "Un généreux anonyme"


@dataclass
class Sections:
    subs: list[tuple[str, str]] = field(default_factory=list)       # (pseudo, précision)
    gifters: list[tuple[str, int]] = field(default_factory=list)    # (pseudo, abonnements offerts)
    cheers: list[tuple[str, int]] = field(default_factory=list)     # (pseudo, bits)
    raids: list[tuple[str, int]] = field(default_factory=list)      # (chaîne, spectateurs)
    follows: list[str] = field(default_factory=list)
    chatters: list[tuple[str, int]] = field(default_factory=list)   # (pseudo, messages)
    silent: list[str] = field(default_factory=list)

    @property
    def total_messages(self) -> int:
        return sum(n for _, n in self.chatters)

    @property
    def is_empty(self) -> bool:
        return not any((self.subs, self.gifters, self.cheers, self.raids, self.follows, self.chatters, self.silent))


def build_sections(
    chatters: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    bots: Iterable[str],
    channel: str,
) -> Sections:
    """Trie les données brutes d'un live. Les bots et la chaîne elle-même ne sont jamais remerciés."""
    excluded = {b.lower() for b in bots} | {channel.lower()}
    s = Sections()
    thanked: set[str] = set()                       # qui est déjà remercié dans une rubrique spéciale

    subs: dict[str, tuple[str, str, int]] = {}      # login -> (nom, précision, mois)
    gifts: dict[str, list] = {}
    cheers: dict[str, list] = {}
    follows: dict[str, str] = {}
    for ev in map(dict, events):
        login, name, kind, amount = str(ev["login"]).lower(), ev["name"], ev["kind"], int(ev["amount"] or 0)
        if login in excluded:
            continue
        if kind in ("sub", "resub"):
            months = amount if kind == "resub" else 0
            note = f"{months} mois" if months > 1 else ("offert" if ev.get("detail") == "gift" else "")
            old = subs.get(login)
            if old is None or months > old[2]:
                subs[login] = (name, note, months)
            thanked.add(login)
        elif kind == "gift":
            key = login or "_anonyme"
            entry = gifts.setdefault(key, [name or ANONYMOUS, 0])
            entry[1] += max(amount, 1)
            thanked.add(login)
        elif kind == "cheer":
            key = login or "_anonyme"
            entry = cheers.setdefault(key, [name or ANONYMOUS, 0])
            entry[1] += amount
            thanked.add(login)
        elif kind == "follow":
            follows.setdefault(login, name)
            thanked.add(login)
        elif kind == "raid":
            s.raids.append((name, amount))
            thanked.add(login)

    s.subs = [(n, note) for n, note, _ in subs.values()]
    s.gifters = sorted(((n, c) for n, c in gifts.values()), key=lambda x: -x[1])
    s.cheers = sorted(((n, c) for n, c in cheers.values()), key=lambda x: -x[1])
    s.follows = list(follows.values())

    for row in map(dict, chatters):
        login = str(row["login"]).lower()
        if login in excluded:
            continue
        if int(row["messages"]) > 0:
            s.chatters.append((row["name"], int(row["messages"])))
        elif int(row["present"]) and login not in thanked:
            s.silent.append(row["name"])
    s.chatters.sort(key=lambda x: (-x[1], x[0].lower()))
    s.silent.sort(key=str.lower)
    return s


def _date_fr(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


# ---------------------------------------------------------------------------
# Réglages de style : une seule description (FIELDS) sert à valider, à rendre et à construire l'éditeur
# ---------------------------------------------------------------------------

FONTS = {
    "sans": ("Sans moderne", '"Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif'),
    "humanist": ("Humaniste", 'Optima, Candara, "Trebuchet MS", "Gill Sans", sans-serif'),
    "rounded": ("Arrondie", '"Varela Round", Nunito, "Trebuchet MS", system-ui, sans-serif'),
    "condensed": ("Condensée", 'Bahnschrift, "Arial Narrow", "Roboto Condensed", sans-serif'),
    "serif": ("Serif", 'Georgia, "Times New Roman", serif'),
    "mono": ("Monospace", '"Cascadia Mono", Consolas, Menlo, "Courier New", monospace'),
}

SECTION_DEFAULTS = (
    ("chatters", "Top du chat"),
    ("gifters", "Abonnements offerts"),
    ("subs", "Abonnés"),
    ("cheers", "Bits"),
    ("raids", "Raids"),
    ("follows", "Followers"),
    ("silent", "Personnes de l'ombre"),
)
CLASSIC_SECTIONS = (
    ("subs", "Les abonnés 💜"),
    ("gifters", "Les généreux donateurs 🎁"),
    ("cheers", "Les bits 💎"),
    ("raids", "Les raids ⚡"),
    ("follows", "Les nouveaux followers"),
    ("chatters", "Ceux qui ont animé le chat 💬"),
    ("silent", "Les personnes de l'ombre 🤫"),
)
SECTION_LABELS = {
    "subs": "Abonnés", "gifters": "Dons d'abonnements", "cheers": "Bits", "raids": "Raids",
    "follows": "Nouveaux followers", "chatters": "Chat", "silent": "Personnes de l'ombre",
}

GLASS = {"template": ["glass"]}
CLASSIC_ONLY = {"template": ["classic"]}

FIELDS: list[dict[str, Any]] = [
    # Modèle
    {"group": "Modèle", "key": "template", "label": "Modèle", "type": "select", "default": "glass",
     "options": [["glass", "Modèle 1 · Verre (cartes et pastilles)"], ["classic", "Modèle 2 · Classique (texte seul)"]]},
    # Couleurs
    {"group": "Couleurs", "key": "accent", "label": "Couleur principale (titres)", "type": "color", "default": "#eef2ff"},
    {"group": "Couleurs", "key": "sub_color", "label": "Couleur d'accent (sous-titre, dons)", "type": "color", "default": "#f7c948"},
    {"group": "Couleurs", "key": "text", "label": "Texte", "type": "color", "default": "#ffffff"},
    {"group": "Couleurs", "key": "muted", "label": "Texte secondaire", "type": "color", "default": "#a8b3d6"},
    {"group": "Couleurs", "key": "bg_mode", "label": "Fond", "type": "select", "default": "transparent",
     "options": [["transparent", "Transparent"], ["solid", "Uni"], ["gradient", "Dégradé"]]},
    {"group": "Couleurs", "key": "bg1", "label": "Couleur du fond", "type": "color", "default": "#0e0e10",
     "show": {"bg_mode": ["solid", "gradient"]}},
    {"group": "Couleurs", "key": "bg2", "label": "Fin du dégradé", "type": "color", "default": "#24164a",
     "show": {"bg_mode": ["gradient"]}},
    {"group": "Couleurs", "key": "bg_opacity", "label": "Opacité du fond", "type": "range", "default": 100,
     "min": 0, "max": 100, "step": 5, "unit": "%", "show": {"bg_mode": ["solid", "gradient"]}},
    {"group": "Couleurs", "key": "shadow", "label": "Ombre sous le texte", "type": "toggle", "default": True},
    # Typographie
    {"group": "Typographie", "key": "font", "label": "Police", "type": "select", "default": "sans",
     "options": [[k, v[0]] for k, v in FONTS.items()]},
    {"group": "Typographie", "key": "font_scale", "label": "Taille du texte", "type": "range", "default": 100,
     "min": 60, "max": 160, "step": 5, "unit": "%"},
    {"group": "Typographie", "key": "title_upper", "label": "Titres en majuscules", "type": "toggle", "default": True},
    {"group": "Typographie", "key": "title_spacing", "label": "Espacement des titres de rubrique", "type": "range", "default": 0.25,
     "min": 0, "max": 0.6, "step": 0.05, "unit": "em"},
    {"group": "Typographie", "key": "title_stretch", "label": "Largeur des lettres du titre du haut", "type": "range", "default": 130,
     "min": 100, "max": 170, "step": 5, "unit": "%"},
    # Cartes et pastilles (modèle 1)
    {"group": "Cartes et pastilles", "key": "top_count", "label": "Cartes « top du chat » (les autres en pastilles)", "type": "range",
     "default": 8, "min": 0, "max": 40, "step": 1, "unit": "", "show": GLASS},
    {"group": "Cartes et pastilles", "key": "card_cols", "label": "Colonnes de cartes", "type": "select", "default": "2",
     "options": [["1", "1 colonne"], ["2", "2 colonnes"], ["3", "3 colonnes"], ["4", "4 colonnes"]], "show": GLASS},
    {"group": "Cartes et pastilles", "key": "show_counts", "label": "Nombre de messages sur les cartes", "type": "toggle", "default": True, "show": GLASS},
    {"group": "Cartes et pastilles", "key": "show_icon", "label": "Icône sur les cartes", "type": "toggle", "default": True, "show": GLASS},
    {"group": "Cartes et pastilles", "key": "count_color", "label": "Couleur du nombre de messages", "type": "color", "default": "#38bdf8", "show": GLASS},
    {"group": "Cartes et pastilles", "key": "card_bg", "label": "Couleur des cartes", "type": "color", "default": "#0b1230", "show": GLASS},
    {"group": "Cartes et pastilles", "key": "card_opacity", "label": "Opacité des cartes", "type": "range", "default": 45,
     "min": 0, "max": 95, "step": 5, "unit": "%", "show": GLASS},
    {"group": "Cartes et pastilles", "key": "card_border", "label": "Liseré clair autour", "type": "toggle", "default": True, "show": GLASS},
    {"group": "Cartes et pastilles", "key": "card_radius", "label": "Arrondi des cartes", "type": "range", "default": 14,
     "min": 0, "max": 40, "step": 2, "unit": "px", "show": GLASS},
    # Défilement
    {"group": "Défilement", "key": "speed", "label": "Vitesse", "type": "range", "default": 7,
     "min": 2, "max": 20, "step": 0.5, "unit": "% écran/s"},
    {"group": "Défilement", "key": "delay", "label": "Délai avant le départ", "type": "range", "default": 1,
     "min": 0, "max": 15, "step": 1, "unit": "s"},
    {"group": "Défilement", "key": "edge_fade", "label": "Fondu en haut et en bas", "type": "range", "default": 12,
     "min": 0, "max": 30, "step": 1, "unit": "% écran"},
    {"group": "Défilement", "key": "ending", "label": "Fin du défilement", "type": "select", "default": "exit",
     "options": [["exit", "Tout sort de l'écran"], ["stay", "La dernière ligne reste affichée"]]},
    {"group": "Défilement", "key": "loop", "label": "Recommencer en boucle", "type": "toggle", "default": False},
    {"group": "Défilement", "key": "loop_pause", "label": "Pause avant de recommencer", "type": "range", "default": 5,
     "min": 0, "max": 60, "step": 1, "unit": "s", "show": {"loop": [True]}},
    # Mise en page
    {"group": "Mise en page", "key": "align", "label": "Alignement", "type": "select", "default": "center",
     "options": [["center", "Centré"], ["left", "À gauche"]]},
    {"group": "Mise en page", "key": "width", "label": "Largeur de la colonne", "type": "range", "default": 50,
     "min": 30, "max": 100, "step": 5, "unit": "%"},
    {"group": "Mise en page", "key": "columns", "label": "Colonnes pour les listes de pseudos", "type": "select", "default": "1",
     "options": [["1", "Flux libre"], ["2", "2 colonnes"], ["3", "3 colonnes"], ["4", "4 colonnes"]], "show": CLASSIC_ONLY},
    # Contenu
    {"group": "Contenu", "key": "show_months", "label": "Ancienneté des abonnés", "type": "toggle", "default": True},
    {"group": "Contenu", "key": "chat_sort", "label": "Classement du chat (hors cartes)", "type": "select", "default": "messages",
     "options": [["messages", "Du plus actif au moins actif"], ["alpha", "Ordre alphabétique"]]},
    {"group": "Contenu", "key": "shadow_names", "label": "Nommer les personnes de l'ombre", "type": "toggle", "default": False},
    # Textes
    {"group": "Textes", "key": "heading", "label": "Titre du haut", "type": "text", "default": "Crédits du live", "max": 40},
    {"group": "Textes", "key": "subtitle", "label": "Sous-titre (couleur d'accent)", "type": "text", "default": "Merci d'avoir regardé", "max": 60},
    {"group": "Textes", "key": "show_title", "label": "Afficher le titre du live", "type": "toggle", "default": False},
    {"group": "Textes", "key": "show_meta", "label": "Afficher la date et les jeux", "type": "toggle", "default": False},
    {"group": "Textes", "key": "closing", "label": "Phrase de fin", "type": "text", "default": "Merci d'avoir été là ❤", "max": 80},
    {"group": "Textes", "key": "show_channel", "label": "Afficher le nom de la chaîne", "type": "toggle", "default": True},
    {"group": "Textes", "key": "shadow_one", "label": "Personnes de l'ombre (une seule)", "type": "text",
     "default": "Merci à la personne de l'ombre, là sans faire de bruit", "max": 120},
    {"group": "Textes", "key": "shadow_many", "label": "Personnes de l'ombre ({n} = nombre)", "type": "text",
     "default": "Merci aux {n} personnes de l'ombre, là sans faire de bruit", "max": 120},
]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
PRESET_LIMIT = 20
PRESET_NAME_MAX = 30


def default_style() -> dict[str, Any]:
    style: dict[str, Any] = {f["key"]: f["default"] for f in FIELDS}
    style["sections"] = [{"id": i, "on": True, "title": t} for i, t in SECTION_DEFAULTS]
    return style


def _normalize_sections(raw: Any) -> list[dict[str, Any]]:
    defaults = dict(SECTION_DEFAULTS)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("id") not in defaults or item["id"] in seen:
            continue
        seen.add(item["id"])
        title = item.get("title")
        title = _CONTROL.sub(" ", title).strip()[:60] if isinstance(title, str) else defaults[item["id"]]
        result.append({"id": item["id"], "on": bool(item.get("on", True)) if isinstance(item.get("on", True), bool) else True, "title": title})
    for sid, title in SECTION_DEFAULTS:              # rubriques absentes : ajoutées à la fin
        if sid not in seen:
            result.append({"id": sid, "on": True, "title": title})
    return result


def normalize_style(raw: Any) -> dict[str, Any]:
    """Réglages validés : toute valeur invalide est remplacée par la valeur par défaut, les clés inconnues sont ignorées."""
    raw = raw if isinstance(raw, dict) else {}
    style = default_style()
    for f in FIELDS:
        key, value = f["key"], raw.get(f["key"], f["default"])
        kind = f["type"]
        if kind == "color":
            style[key] = value.lower() if isinstance(value, str) and _HEX.match(value) else f["default"]
        elif kind == "range":
            try:
                num = float(value)
            except (TypeError, ValueError):
                num = float(f["default"])
            if num != num or num in (float("inf"), float("-inf")) or isinstance(value, bool):
                num = float(f["default"])
            num = min(max(num, f["min"]), f["max"])
            style[key] = int(num) if float(f["step"]).is_integer() and num == int(num) else round(num, 3)
        elif kind == "select":
            allowed = [o[0] for o in f["options"]]
            style[key] = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) in allowed else f["default"]
        elif kind == "toggle":
            style[key] = value if isinstance(value, bool) else f["default"]
        elif kind == "text":
            style[key] = _CONTROL.sub(" ", value).strip()[: f["max"]] if isinstance(value, str) else f["default"]
    style["sections"] = _normalize_sections(raw.get("sections"))
    return style


# Réglages du modèle 2 (l'ancien rendu en texte seul) ; les préréglages suivants en partent.
CLASSIC: dict[str, Any] = {
    "template": "classic", "accent": "#9b7bff", "sub_color": "#f2c76e", "text": "#f4f3f8", "muted": "#a9a4bd",
    "title_spacing": 0.3, "title_stretch": 100, "edge_fade": 0, "width": 80, "heading": "Merci", "subtitle": "",
    "show_title": True, "show_meta": True,
    "sections": [{"id": i, "on": True, "title": t} for i, t in CLASSIC_SECTIONS],
}
PRESETS: dict[str, dict[str, Any]] = {
    "Modèle 1 · Verre": {},
    "Modèle 2 · Classique": CLASSIC,
    "Clair": {
        **CLASSIC, "bg_mode": "solid", "bg1": "#f6f5f1", "text": "#1d1b26", "muted": "#6b6780", "accent": "#5b3fd0",
        "sub_color": "#b4730b", "shadow": False,
    },
    "Chaleureux": {
        **CLASSIC, "bg_mode": "gradient", "bg1": "#2a1410", "bg2": "#5c2a1e", "accent": "#ff9b54", "sub_color": "#ffd9a0",
        "text": "#fff4e8", "muted": "#e0bfa8", "font": "serif",
    },
    "Minimal": {
        **CLASSIC, "accent": "#ffffff", "sub_color": "#ffffff", "muted": "#9a9aa5", "title_upper": False, "title_spacing": 0.05,
        "align": "left", "width": 60, "speed": 6,
    },
    "Néon": {
        **CLASSIC, "bg_mode": "solid", "bg1": "#07060d", "accent": "#00e5ff", "sub_color": "#ff4fd8", "text": "#eafcff",
        "muted": "#7aa7b3", "font": "condensed", "title_spacing": 0.35,
    },
}


def preset_style(name: str) -> dict[str, Any]:
    return normalize_style(PRESETS[name])


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------

def _names(items: Iterable[str], css: str = "") -> str:
    spans = "".join(f"<span>{html.escape(n)}</span>" for n in items)
    return f'<div class="names {css}">{spans}</div>'


def _lines(rows: Iterable[tuple[str, str]]) -> str:
    return '<div class="lines">' + "".join(f"<div>{html.escape(n)}<small>{html.escape(note)}</small></div>" for n, note in rows) + "</div>"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Crédits</title>
<style>
:root { --accent: __ACCENT__; --gold: __GOLD__; --text: __TEXT__; --muted: __MUTED__; --font: __FONT__; --ls: __LS__em; }
html { font-size: __FONTSIZE__vh; }
html, body { margin: 0; height: 100%; overflow: hidden; background: __BACKGROUND__; color: var(--text); font-family: var(--font); }
#view { position: fixed; inset: 0; overflow: hidden; __MASK__ }
#credits { position: absolute; left: 0; right: 0; top: 0; will-change: transform; __SHADOW__ }
.col { width: __WIDTH__%; margin: 0 auto; text-align: __ALIGN__; }
header { padding-bottom: 3rem; }
header h1 { margin: 0; font-size: 3.6rem; letter-spacing: var(--ls); text-transform: __UPPER__; color: var(--accent); }
header .sub { margin-top: .8rem; color: var(--gold); font-size: 1.3rem; }
header .stream { margin-top: 1rem; font-size: 1.5rem; }
header .meta { margin-top: .4rem; color: var(--muted); font-size: 1.1rem; }
section { padding: 1.6rem 0; }
h2 { margin: 0 0 1rem; font-size: 1.2rem; letter-spacing: var(--ls); text-transform: __UPPER__; color: var(--accent); }
.names span { display: inline-block; margin: .25rem .9rem; font-size: 1.5rem; }
.names.big span { font-size: 2.1rem; color: var(--gold); font-weight: 700; }
.names.small span { font-size: 1.15rem; color: var(--muted); margin: .15rem .7rem; }
.names small { color: var(--muted); font-weight: 400; font-size: .7em; margin-left: .4em; }
.col.left .names span { margin-left: 0; margin-right: 1.4rem; }
.names.cols { column-count: __COLUMNS__; column-gap: 2rem; }
.names.cols span { display: block; margin: .2rem 0; }
.lines div { margin: .4rem 0; font-size: 1.5rem; }
.lines small { color: var(--muted); font-size: .75em; margin-left: .5em; }
footer { padding: 4rem 0 2rem; }
footer .thanks { font-size: 3rem; font-weight: 700; }
footer .channel { margin-top: .6rem; font-size: 1.6rem; color: var(--accent); }
__EXTRA_CSS__
</style>
</head>
<body>
<div id="view"><div id="credits"><div class="col __ALIGNCLASS__">
__CONTENT__
</div></div></div>
<script>
const CFG = __CFG__;
const el = document.getElementById('credits');
let anim = null;
function play() {
  if (CFG.static) return;
  if (anim) anim.cancel();
  const vh = window.innerHeight, h = el.offsetHeight, foot = el.querySelector('footer');
  let endY = -h;
  if (CFG.ending === 'stay' && foot) endY = Math.min(0, vh / 2 - (foot.offsetTop + foot.offsetHeight / 2));
  const duration = ((vh - endY) / (vh * CFG.speed / 100)) * 1000;
  anim = el.animate(
    [{ transform: 'translateY(' + vh + 'px)' }, { transform: 'translateY(' + endY + 'px)' }],
    { duration: duration, delay: CFG.delay * 1000, easing: 'linear', fill: 'both' }
  );
  if (CFG.loop) anim.finished.then(function () { setTimeout(play, CFG.loop_pause * 1000); }, function () {});
}
window.addEventListener('load', play);
</script>
</body>
</html>
"""

GLASS_CSS = """
:root { --count: __COUNT__; }
header { padding-bottom: 2.6rem; }
header h1 { display: inline-block; font-size: 3.3rem; font-weight: 900; letter-spacing: .04em; transform: scaleX(__STRETCH__); transform-origin: center; }
.col.left header h1 { transform-origin: left center; }
header .sub { margin-top: .9rem; font-size: 1.05rem; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; }
header .stream { margin-top: .8rem; font-size: 1.1rem; color: var(--muted); }
header .meta { font-size: .95rem; }
section { padding: 1.1rem 0; }
h2 { display: flex; align-items: center; gap: 1rem; margin: 0 0 1.1rem; font-size: 1.05rem; font-weight: 700; }
h2::before, h2::after { content: ""; flex: 1; height: 1px; background: linear-gradient(90deg, transparent, rgba(255,255,255,.28)); }
h2::after { background: linear-gradient(270deg, transparent, rgba(255,255,255,.28)); }
.col.left h2::before { display: none; }
.cards { display: grid; grid-template-columns: repeat(__CARDCOLS__, minmax(0, 1fr)); gap: .7rem; margin-bottom: .9rem; text-align: left; }
.card { display: flex; align-items: center; gap: .75rem; padding: .7rem .95rem; border-radius: __RADIUS__px; background: __CARDBG__; border: __CARDBORDER__;
  box-shadow: 0 6px 22px rgba(0,0,0,.28); }
.card .ico { flex: none; display: grid; place-items: center; width: 2.1rem; height: 2.1rem; border-radius: .6rem; background: rgba(145,70,255,.3); }
.card .ico svg { width: 1.15rem; height: 1.15rem; fill: #c4a4ff; }
.card .txt { min-width: 0; }
.card b { display: block; font-size: 1.15rem; line-height: 1.2; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card small { display: block; margin-top: .1rem; color: var(--count); font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.pills { display: flex; flex-wrap: wrap; justify-content: center; gap: .6rem; }
.col.left .pills { justify-content: flex-start; }
.pill { display: inline-flex; align-items: baseline; gap: .55rem; padding: .4rem 1.05rem; border-radius: 999px; background: __CARDBG__; border: __CARDBORDER__;
  font-size: 1.1rem; font-weight: 600; }
.pill small { font-size: .7rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--gold); }
.pill small.mut { color: var(--muted); }
.pill.sm { font-size: .95rem; font-weight: 500; color: var(--muted); }
.say { margin: 0; color: var(--muted); font-size: 1.15rem; }
footer { padding: 3rem 0 2rem; }
footer .thanks { font-size: 2.2rem; }
footer .channel { font-size: 1.3rem; letter-spacing: .15em; text-transform: uppercase; }
"""

TWITCH_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 3h17v11.5l-5 4.5h-4l-3 3v-3H4zm2 2v12h4v1.2L12 16h3.2L19 12.6V5z"/><path d="M11 8h1.8v4.5H11zm4 0h1.8v4.5H15z"/></svg>'


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha:.2f})"


def _pills(items: Iterable[tuple[str, str, str]]) -> str:
    """items : (texte, petite mention, classes de la mention)."""
    out = ""
    for name, note, css in items:
        small = f'<small class="{css}">{html.escape(note)}</small>' if note else ""
        out += f'<span class="pill">{html.escape(name)}{small}</span>'
    return f'<div class="pills">{out}</div>'



def _classic_blocks(sections: Sections, st: Mapping[str, Any], names: Any, cols: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    if sections.subs:
        spans = "".join(
            "<span>" + html.escape(n)
            + (f"<small>{html.escape(note)}</small>" if note and (st["show_months"] or not note.endswith(" mois")) else "")
            + "</span>"
            for n, note in sections.subs
        )
        blocks["subs"] = f'<div class="names big {cols}">{spans}</div>'
    if sections.gifters:
        blocks["gifters"] = _lines((n, f"{_plural(c, 'abonnement offert', 'abonnements offerts')}") for n, c in sections.gifters)
    if sections.cheers:
        blocks["cheers"] = _lines((n, f"{c} bits") for n, c in sections.cheers)
    if sections.raids:
        blocks["raids"] = _lines((n, _plural(c, "spectateur", "spectateurs")) for n, c in sections.raids)
    if sections.follows:
        blocks["follows"] = names(sections.follows)
    if sections.chatters:
        pseudos = [n for n, _ in sections.chatters]
        if st["chat_sort"] == "alpha":
            pseudos.sort(key=str.lower)
        blocks["chatters"] = names(pseudos)
    if sections.silent:
        if st["shadow_names"]:
            blocks["silent"] = names(sections.silent, "small")
        else:
            n = len(sections.silent)
            text = st["shadow_one"] if n == 1 else st["shadow_many"].replace("{n}", str(n))
            blocks["silent"] = f'<div class="lines"><div>{html.escape(text, quote=False)}</div></div>'
    return blocks


def _glass_blocks(sections: Sections, st: Mapping[str, Any]) -> dict[str, str]:
    blocks: dict[str, str] = {}
    if sections.chatters:
        top = sections.chatters[: int(st["top_count"])]
        rest = [n for n, _ in sections.chatters[int(st["top_count"]):]]
        if st["chat_sort"] == "alpha":
            rest.sort(key=str.lower)
        html_out = ""
        if top:
            cards = ""
            for n, c in top:
                icon = f'<span class="ico">{TWITCH_ICON}</span>' if st["show_icon"] else ""
                count = f"<small>{_plural(c, 'message', 'messages')}</small>" if st["show_counts"] else ""
                cards += f'<div class="card">{icon}<span class="txt"><b>{html.escape(n)}</b>{count}</span></div>'
            html_out += f'<div class="cards">{cards}</div>'
        if rest:
            html_out += _pills((n, "", "") for n in rest)
        blocks["chatters"] = html_out
    if sections.gifters:
        blocks["gifters"] = _pills((n, _plural(c, "sub offert", "subs offerts"), "") for n, c in sections.gifters)
    if sections.subs:
        blocks["subs"] = _pills(
            (n, note if (st["show_months"] or not note.endswith(" mois")) else "", "mut") for n, note in sections.subs
        )
    if sections.cheers:
        blocks["cheers"] = _pills((n, f"{c} bits", "") for n, c in sections.cheers)
    if sections.raids:
        blocks["raids"] = _pills((n, _plural(c, "spectateur", "spectateurs"), "mut") for n, c in sections.raids)
    if sections.follows:
        blocks["follows"] = _pills((n, "", "") for n in sections.follows)
    if sections.silent:
        if st["shadow_names"]:
            blocks["silent"] = '<div class="pills">' + "".join(f'<span class="pill sm">{html.escape(n)}</span>' for n in sections.silent) + "</div>"
        else:
            n = len(sections.silent)
            text = st["shadow_one"] if n == 1 else st["shadow_many"].replace("{n}", str(n))
            blocks["silent"] = f'<p class="say">{html.escape(text, quote=False)}</p>'
    return blocks


def render_credits(
    sections: Sections,
    *,
    channel_name: str,
    title: str = "",
    games: Iterable[str] = (),
    started_at: str | None = None,
    style: Mapping[str, Any] | None = None,
    mode: str = "play",
) -> str:
    """Page HTML autonome (aucune ressource externe) à ajouter dans OBS comme source navigateur.

    `mode="static"` : pas d'animation, page défilable (aperçu de l'éditeur).
    """
    st = normalize_style(style)
    games = [g for g in games if g]

    head = "<header>"
    if st["heading"]:
        head += f'<h1>{html.escape(st["heading"], quote=False)}</h1>'
    if st["subtitle"]:
        head += f'<div class="sub">{html.escape(st["subtitle"], quote=False)}</div>'
    if title and st["show_title"]:
        head += f'<div class="stream">{html.escape(title, quote=False)}</div>'
    meta = " — ".join(p for p in (_date_fr(started_at), " · ".join(games)) if p)
    if meta and st["show_meta"]:
        head += f'<div class="meta">{html.escape(meta, quote=False)}</div>'
    head += "</header>"

    cols = "cols" if int(st["columns"]) > 1 else ""

    def names(items: Iterable[str], css: str = "") -> str:
        return _names(items, f"{css} {cols}".strip())

    blocks: dict[str, str] = {}
    if st["template"] == "glass":
        blocks = _glass_blocks(sections, st)
    else:
        blocks = _classic_blocks(sections, st, names, cols)

    parts = [head]
    for sec in st["sections"]:
        if sec["on"] and sec["id"] in blocks:
            parts.append(f'<section><h2>{html.escape(sec["title"], quote=False)}</h2>{blocks[sec["id"]]}</section>')
    foot = ""
    if st["closing"]:
        foot += f'<div class="thanks">{html.escape(st["closing"], quote=False)}</div>'
    if st["show_channel"] and channel_name:
        foot += f'<div class="channel">{html.escape(channel_name, quote=False)}</div>'
    parts.append(f"<footer>{foot}</footer>")

    alpha = st["bg_opacity"] / 100
    if st["bg_mode"] == "solid":
        background = _rgba(st["bg1"], alpha)
    elif st["bg_mode"] == "gradient":
        background = f'linear-gradient(180deg, {_rgba(st["bg1"], alpha)}, {_rgba(st["bg2"], alpha)})'
    else:
        background = "transparent"
    static = mode == "static"
    cfg = {"speed": st["speed"], "delay": st["delay"], "ending": st["ending"], "loop": st["loop"],
           "loop_pause": st["loop_pause"], "static": static}
    replacements = {
        "__ACCENT__": st["accent"], "__GOLD__": st["sub_color"], "__TEXT__": st["text"], "__MUTED__": st["muted"],
        "__FONT__": FONTS[st["font"]][1], "__LS__": str(st["title_spacing"]),
        "__FONTSIZE__": f'{2.4 * st["font_scale"] / 100:.3f}', "__BACKGROUND__": background,
        "__SHADOW__": "text-shadow: 0 2px 8px rgba(0,0,0,.6);" if st["shadow"] else "",
        "__WIDTH__": str(st["width"]), "__ALIGN__": st["align"], "__ALIGNCLASS__": "left" if st["align"] == "left" else "",
        "__UPPER__": "uppercase" if st["title_upper"] else "none", "__COLUMNS__": str(int(st["columns"])),
        "__MASK__": (f"-webkit-mask-image: linear-gradient(to bottom, transparent, #000 {st['edge_fade']}%, #000 {100 - st['edge_fade']}%, transparent); "
                     f"mask-image: linear-gradient(to bottom, transparent, #000 {st['edge_fade']}%, #000 {100 - st['edge_fade']}%, transparent);")
        if st["edge_fade"] else "",
        "__EXTRA_CSS__": (GLASS_CSS if st["template"] == "glass" else "")
        + ("html, body { overflow: auto; } #view { position: static; overflow: visible; -webkit-mask-image: none; mask-image: none; } "
           "#credits { position: static; padding: 2rem 0; }" if static else ""),
        "__COUNT__": st["count_color"], "__STRETCH__": f'{st["title_stretch"] / 100:.2f}', "__CARDCOLS__": str(int(st["card_cols"])),
        "__RADIUS__": str(st["card_radius"]), "__CARDBG__": _rgba(st["card_bg"], st["card_opacity"] / 100),
        "__CARDBORDER__": "1px solid rgba(255,255,255,.14)" if st["card_border"] else "1px solid transparent",
        "__CFG__": json.dumps(cfg), "__CONTENT__": "\n".join(parts),
    }
    page = TEMPLATE
    for _ in range(2):                       # 2 passes : __EXTRA_CSS__ contient lui-même des jetons
        for key, value in replacements.items():
            page = page.replace(key, value)
    return page


# ---------------------------------------------------------------------------
# Données factices pour l'aperçu de l'éditeur
# ---------------------------------------------------------------------------

DEMO_INFO = {
    "channel_name": "ZenoSurTwitch_",
    "title": "Soirée découverte — premier boss",
    "games": ["Hades", "Celeste"],
    "started_at": "2026-11-07T18:00:00Z",
}


def demo_sections() -> Sections:
    chat = ["Alice", "Bob_le_Bricoleur", "XxDarkGamerxX", "Mélodie", "Zoé", "Kevin", "Lucie", "Théo", "Camille", "Nolan",
            "Inès", "Hugo", "Manon", "Léo", "Sarah", "Yanis", "Jade", "Maxime", "Clara", "Enzo", "Lola", "Adam"]
    rows = [{"login": n.lower(), "name": n, "messages": 40 - 2 * i, "present": 1} for i, n in enumerate(chat)]
    rows += [{"login": f"ombre{i}", "name": f"Ombre{i}", "messages": 0, "present": 1} for i in range(1, 24)]
    events = [{"kind": "sub", "login": n.lower(), "name": n, "amount": 0, "detail": None} for n in ("Léa", "Marc", "Sofia")]
    events += [
        {"kind": "resub", "login": "alice", "name": "Alice", "amount": 14, "detail": None},
        {"kind": "resub", "login": "theo", "name": "Théo", "amount": 3, "detail": None},
        {"kind": "gift", "login": "kevin", "name": "Kevin", "amount": 5, "detail": None},
        {"kind": "cheer", "login": "lucie", "name": "Lucie", "amount": 300, "detail": None},
        {"kind": "raid", "login": "jules", "name": "JulesLeStreamer", "amount": 12, "detail": None},
    ]
    events += [{"kind": "follow", "login": f"f{i}", "name": n, "amount": 0, "detail": None}
               for i, n in enumerate(("Nora", "Paul", "Quentin", "Rose", "Samir"))]
    return build_sections(rows, events, [], "zeno")
