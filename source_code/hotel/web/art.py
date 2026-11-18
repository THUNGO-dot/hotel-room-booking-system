"""Room illustrations, ported from the HTML prototype."""
from __future__ import annotations

from markupsafe import Markup, escape

HERO = Markup(
    '<svg class="art" viewBox="0 0 520 380" preserveAspectRatio="xMaxYMid slice" aria-hidden="true"><defs>'
    '<linearGradient id="hk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f6e2b9"/><stop offset=".62" stop-color="#eeb27a"/><stop offset="1" stop-color="#d98a62"/></linearGradient>'
    '<linearGradient id="hs" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2e6f6a"/><stop offset="1" stop-color="#0f3a37"/></linearGradient>'
    '<clipPath id="hc"><path d="M40 380V150a70 70 0 0 1 140 0v230Z"/><path d="M200 380V110a70 70 0 0 1 140 0v270Z"/><path d="M360 380V190a70 70 0 0 1 140 0v190Z"/></clipPath></defs>'
    '<g clip-path="url(#hc)"><rect width="520" height="380" fill="url(#hk)"/><circle cx="270" cy="236" r="40" fill="#fbf0d4"/><rect y="236" width="520" height="150" fill="url(#hs)"/>'
    '<g stroke="#f3d79c" stroke-opacity=".55" stroke-width="2" stroke-linecap="round"><path d="M236 252h68M246 264h48M256 276h28M60 258h90M390 262h80M410 276h44"/></g></g>'
    '<g fill="none" stroke="#b8935a" stroke-width="1.6"><path d="M40 380V150a70 70 0 0 1 140 0v230M200 380V110a70 70 0 0 1 140 0v270M360 380V190a70 70 0 0 1 140 0v190"/></g></svg>')

ART = {
    "dlx": dict(wall="#e9e3d5", floor="#cfc4ad", bed="#14372f", beds=[(96, 288)], tables=[40, 398],
                view='<defs><linearGradient id="a1-{u}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f6e2b9"/><stop offset="1" stop-color="#eeb27a"/></linearGradient></defs><rect x="160" y="20" width="160" height="140" fill="url(#a1-{u})"/><circle cx="240" cy="108" r="22" fill="#fbf0d4"/><rect x="160" y="108" width="160" height="60" fill="#2e6f6a"/><path d="M190 120h60M210 130h40M232 140h60" stroke="#f3d79c" stroke-opacity=".6" stroke-width="2"/>'),
    "std": dict(wall="#e4e6df", floor="#c9c3b0", bed="#7a6338", beds=[(120, 240)], tables=[64, 374],
                view='<rect x="160" y="20" width="160" height="140" fill="#d8e2dd"/><g fill="#6f8a84"><rect x="168" y="96" width="26" height="64"/><rect x="198" y="72" width="30" height="88"/><rect x="232" y="104" width="22" height="56"/><rect x="258" y="84" width="28" height="76"/><rect x="290" y="110" width="26" height="50"/></g><g fill="#f3e6c4"><rect x="204" y="82" width="5" height="6"/><rect x="214" y="96" width="5" height="6"/><rect x="264" y="94" width="5" height="6"/><rect x="204" y="110" width="5" height="6"/></g>'),
    "exe": dict(wall="#e6e0d1", floor="#cbbfa6", bed="#2f5b6b", beds=[(56, 172), (252, 172)], tables=[222],
                view='<rect x="160" y="20" width="160" height="140" fill="#d5e3df"/><path d="M160 112c30-26 50-26 80-6s56 6 80-12v66H160Z" fill="#7da08d"/><path d="M160 134c40-20 70-14 100 0s40 4 60-8v34H160Z" fill="#3f7360"/><g stroke="#fff" stroke-width="2.5"><path d="M160 130h160M174 130v30M200 130v30M226 130v30M252 130v30M278 130v30M304 130v30"/></g>'),
}


def art_key(name: str) -> str:
    n = (name or "").lower()
    if "executive" in n or "family" in n:
        return "exe"
    if "deluxe" in n or "ocean" in n:
        return "dlx"
    return "std"


def room_art(name: str, uid: str | int = "") -> Markup:
    key = art_key(name)
    a = ART[key]
    u = f"{key}{uid}"

    def bed(x, w):
        half = (w - 30) / 2
        return (f'<rect x="{x}" y="150" width="{w}" height="100" rx="5" fill="#7b5d3b"/>'
                f'<rect x="{x + 10}" y="168" width="{half}" height="36" rx="12" fill="#fff"/>'
                f'<rect x="{x + 20 + half}" y="168" width="{half}" height="36" rx="12" fill="#f7f3ea"/>'
                f'<rect x="{x - 12}" y="198" width="{w + 24}" height="54" rx="8" fill="#f4f0e6"/>'
                f'<rect x="{x - 12}" y="216" width="{w + 24}" height="38" rx="6" fill="{a["bed"]}"/>'
                f'<rect x="{x - 12}" y="224" width="{w + 24}" height="4" fill="#b8935a"/>')

    def table(x):
        return (f'<rect x="{x}" y="200" width="42" height="46" rx="3" fill="#7b5d3b"/>'
                f'<path d="M{x + 8} 192l5-26h16l5 26Z" fill="#f3ead8" stroke="#b8935a" stroke-width="1.5"/>'
                f'<path d="M{x + 21} 192v8" stroke="#b8935a" stroke-width="2"/>')

    view = a["view"].replace("{u}", u)
    svg = (f'<svg class="rm" viewBox="0 0 480 300" preserveAspectRatio="xMidYMid slice" role="img" '
           f'aria-label="Illustration of the {escape(name)}"><rect width="480" height="300" fill="{a["wall"]}"/>'
           f'<rect y="238" width="480" height="62" fill="{a["floor"]}"/><rect y="236" width="480" height="3" fill="#b8935a"/>'
           f'<clipPath id="w-{u}"><path d="M160 168V90a80 80 0 0 1 160 0v78Z"/></clipPath>'
           f'<g clip-path="url(#w-{u})">{view}</g>'
           '<path d="M160 168V90a80 80 0 0 1 160 0v78Z" fill="none" stroke="#fbfaf5" stroke-width="5"/>'
           '<path d="M240 10v158" stroke="#fbfaf5" stroke-width="3" opacity=".7"/>'
           '<rect x="122" y="30" width="30" height="142" fill="#d8cfb9"/><rect x="328" y="30" width="30" height="142" fill="#d8cfb9"/>'
           '<g stroke="#c3b89d" stroke-width="2"><path d="M130 30v142M138 30v142M146 30v142M336 30v142M344 30v142M352 30v142"/></g>'
           + "".join(bed(x, w) for x, w in a["beds"]) + "".join(table(x) for x in a["tables"]) + "</svg>")
    return Markup(svg)
