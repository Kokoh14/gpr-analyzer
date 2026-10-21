"""Self-contained HTML renderer (figures inlined as base64). Used for previews and the web view."""

from __future__ import annotations

import base64
import os
from html import escape
from pathlib import Path

from gpr_analyzer.report.compose import Bullets, Figure, Heading, PageBreak, Para, Report, Table

CSS = """
:root{--bg:#f6f7f9;--paper:#fff;--text:#1c2026;--muted:#5f6670;--line:#d9dde3;--head:#e8ecf1;
--aE:#f8d7da;--a1:#fde2cc;--a2:#fff3cd;--a3:#d6e6fa;--anone:transparent;--warn:#fff3cd;--warnt:#6b4e00}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121417;--paper:#1b1e23;--text:#e6e8eb;
--muted:#a0a7b1;--line:#343a42;--head:#262b32;--aE:#5a1f27;--a1:#5a3417;--a2:#4d4214;--a3:#1d3557;--warn:#4d4214;--warnt:#ffe6a3;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121417;--paper:#1b1e23;--text:#e6e8eb;--muted:#a0a7b1;--line:#343a42;--head:#262b32;
--aE:#5a1f27;--a1:#5a3417;--a2:#4d4214;--a3:#1d3557;--warn:#4d4214;--warnt:#ffe6a3;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font-family:"Sarabun","Leelawadee UI","Noto Sans Thai",system-ui,sans-serif;font-size:15px;line-height:1.55;
padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
main{max-width:960px;margin:24px auto;padding:28px 32px;background:var(--paper);border:1px solid var(--line);border-radius:10px}
h1{font-size:1.6rem;margin:0 0 4px;line-height:1.3;text-wrap:balance}h2{font-size:1.25rem;margin:2rem 0 .6rem;padding-top:.4rem;border-top:1px solid var(--line)}
h3{font-size:1.08rem;margin:1.4rem 0 .5rem}h4{font-size:.98rem;margin:1.1rem 0 .4rem}
.sub{color:var(--muted);margin:0 0 12px}.draft{background:var(--warn);color:var(--warnt);padding:8px 12px;border-radius:6px;font-weight:600}
p{margin:.35rem 0}.secondary{color:var(--muted);font-style:italic}.small,.caption{color:var(--muted);font-size:.88rem}
ul{margin:.3rem 0 .8rem;padding-left:1.2rem}li{margin:.15rem 0}
.tw{overflow-x:auto;margin:.4rem 0 1rem}table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}th{background:var(--head)}
table.kv td:first-child{color:var(--muted);font-weight:600;width:28%}table.kv td{border-width:0 0 1px 0}
td.aE{background:var(--aE)}td.a1{background:var(--a1)}td.a2{background:var(--a2)}td.a3{background:var(--a3)}
figure{margin:.6rem 0 1rem}figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:4px;background:#fff}
figcaption{color:var(--muted);font-size:.86rem}footer{color:var(--muted);font-size:.8rem;margin-top:2rem}
@media (max-width:640px){main{margin:12px 0;padding:16px 0;border-width:0;background:transparent}}
td{font-variant-numeric:tabular-nums}
"""


def render_html(report: Report, path: str | os.PathLike | None = None, fragment: bool = False,
                page_title: str | None = None) -> str:
    """fragment=True leaves out doctype/html/head/body, for hosts that add their own skeleton."""
    e = escape
    head = ['<link rel="preconnect" href="https://fonts.googleapis.com">',
            '<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap" rel="stylesheet">',
            f"<title>{e(page_title or report.title)}</title><style>{CSS}</style>"]
    if fragment:
        out = ['<meta name="claude:viewport-fit-sides" content="cover">', *head, "<main>"]
    else:
        out = [f'<!doctype html><html lang="{"en" if report.lang == "en" else "th"}"><head><meta charset="utf-8">',
               '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
               *head, "</head><body><main>"]
    out.append(f"<h1>{e(report.title)}</h1><p class=sub>{e(report.subtitle)}</p>")
    if report.draft:
        out.append(f'<p class=draft>{"<br>".join(e(t) for t in report.draft_text)}</p>')
    for b in report.blocks:
        if isinstance(b, Heading):
            n = min(b.level + 1, 4)
            out.append(f"<h{n}>{e(b.text)}</h{n}>")
        elif isinstance(b, Para):
            out.append(f'<p class="{b.style}">{e(b.text)}</p>')
        elif isinstance(b, Bullets):
            out.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in b.items) + "</ul>")
        elif isinstance(b, Table):
            rows = []
            if b.header:
                rows.append("<tr>" + "".join(f"<th>{e(h)}</th>" for h in b.header) + "</tr>")
            for i, r in enumerate(b.rows):
                tag = b.row_tags[i] if b.row_tags else None
                cells = []
                for j, c in enumerate(r):
                    cls = f' class="a{tag}"' if tag and j == 0 and tag != "none" else ""
                    cells.append(f"<td{cls}>{e(c)}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            kind = ' class="kv"' if b.kind == "kv" else ""
            out.append(f'<div class=tw><table{kind}>{"".join(rows)}</table></div>')
        elif isinstance(b, Figure):
            src = "data:image/png;base64," + base64.b64encode(b.png).decode()
            out.append(f'<figure><img src="{src}" alt="{e(b.caption[0])}"><figcaption>'
                       + "<br>".join(e(c) for c in b.caption) + "</figcaption></figure>")
        elif isinstance(b, PageBreak):
            continue
    out.append(f"<footer>{e(report.footer)}</footer></main>")
    if not fragment:
        out.append("</body></html>")
    html = "".join(out)
    if path is not None:
        Path(path).write_text(html, encoding="utf-8")
    return html
