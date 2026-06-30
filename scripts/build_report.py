#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_report.py  --  visual HTML report generator for the `rare-disease-epi` skill.

    python scripts/build_report.py <data.json> -o <output.html>

Reads a structured JSON document (schema: templates/report_schema.json) that Claude
fills in AFTER fetch_epi_data.py + web + the pause gates, and renders a single,
self-contained HTML file (inline CSS + inline SVG, NO runtime deps, opens offline,
prints cleanly to PDF).

Rules (match SKILL.md): every value keeps its badge(s); red stays loud; the
"需人工核对清单" block is always rendered; epi tables keep the 10-column caliber
(year / sample size / patient type first); all source links collect into a final
"参考来源 (Reference list)" section. Pretty never overrides honest. Numbers are
never averaged across studies.

Badge keys: official(🏛️官方·登记数) | db(✅库) | guideline(📘指南/共识) | web(🔍web)
            | weak(⚠️) | redflag(🔴) | na(❓N/A)
"""

import sys
import json
import html
import argparse
from datetime import datetime, timezone

BADGES = {
    "official":  ("\U0001F3DB️官方", "b-official"),
    "db":        ("✅库", "b-db"),
    "guideline": ("\U0001F4D8指南", "b-guide"),
    "web":       ("\U0001F50Dweb", "b-web"),
    "weak":      ("⚠️存疑", "b-weak"),
    "redflag":   ("\U0001F534必核", "b-red"),
    "na":        ("❓N/A", "b-na"),
}
ALIASES = {
    "gov": "official", "registry": "official", "regulator": "official",
    "database": "db", "orphanet": "db", "pubmed": "db", "ctgov": "db",
    "guide": "guideline", "consensus": "guideline", "genereviews": "guideline",
    "media": "web", "pmid": "web", "secondary": "weak", "unknown": "na",
    "notfound": "na", "red": "redflag",
}
BADGE_FILL = {
    "official": "#2b4a6f", "db": "#35637a", "guideline": "#4a5a82", "web": "#5c748e",
    "weak": "#8a7b5e", "redflag": "#9d4b4b", "na": "#94a3b8",
}
BADGE_PREF = ("redflag", "official", "db", "guideline", "web", "weak", "na")


def _norm(key):
    if not key:
        return None
    k = str(key).strip()
    return k if k in BADGES else ALIASES.get(k.lower(), None)


def chip(key):
    nk = _norm(key)
    if not nk:
        return ""
    label, cls = BADGES[nk]
    return '<span class="chip {}">{}</span>'.format(cls, html.escape(label))


def chips(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "".join(chip(v) for v in value)
    return chip(value)


def esc(x):
    return html.escape("" if x is None else str(x))


def _badge_color(badge):
    if isinstance(badge, (list, tuple)):
        for pref in BADGE_PREF:
            if any(_norm(b) == pref for b in badge):
                return BADGE_FILL[pref]
        return "#334155"
    return BADGE_FILL.get(_norm(badge), "#334155")


def year_to_float(y):
    if not y:
        return None
    import re
    m = re.search(r"(\d{4})", str(y))
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
def render_header(meta, identity):
    disease = esc(meta.get("disease") or meta.get("name") or "未命名罕见病")
    sub_bits = []
    for k, pre in (("orphacode", ""), ("omim", "OMIM "), ("icd10", "ICD-10 "), ("icd11", "ICD-11 ")):
        if meta.get(k):
            sub_bits.append(pre + esc(meta[k]))
    sub = "  ·  ".join(sub_bits)
    gen = esc(meta.get("generated") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    cmd = esc(meta.get("script_cmd") or "")
    mode = esc(meta.get("mode") or "")
    anchor = esc(meta.get("consensus_anchor") or "")

    rows = ""
    for item in (identity or []):
        rows += '<tr><td class="k">{}</td><td class="v">{} {}</td></tr>'.format(
            esc(item.get("field")), esc(item.get("value")), chips(item.get("badge")))
    id_table = '<table class="idtable"><tbody>{}</tbody></table>'.format(rows) if rows else ""

    skip = ""
    if meta.get("skipped_gates"):
        skip = ('<div class="banner red">⚠️ 已按用户要求"一次跑完"，未逐项停下确认；'
                '下面所有标 \U0001F534 的地方都还没经过人工核对。</div>')

    meta_bits = ["生成时间 " + gen]
    if mode:
        meta_bits.append("范围：" + mode)
    if cmd:
        meta_bits.append("数据脚本 <code>" + cmd + "</code>")
    meta_line = "  ·  ".join(meta_bits)
    anchor_html = ('<div class="anchor">\U0001F4D8 共识锚点：{}</div>'.format(anchor)) if anchor else ""

    return """
<header class="hero">
  <div class="hero-eyebrow">罕见病流行病学情报 · International \U0001F30D + 美国 \U0001F1FA\U0001F1F8 + 中国 \U0001F1E8\U0001F1F3</div>
  <h1>{disease}</h1>
  <div class="hero-sub">{sub}</div>
  <div class="hero-meta">{meta_line}</div>
  {anchor}
  <div class="banner amber">⚠️ 仅供研究情报参考，<b>非医疗建议、非诊断</b>。每个 epi 数字须带年份+样本量+徽标；标 \U0001F534 项必人工复核。</div>
  {skip}
  {id_table}
</header>
""".format(disease=disease, sub=sub, meta_line=meta_line, anchor=anchor_html, skip=skip, id_table=id_table)


def render_legend():
    notes = {
        "official": "各国官方疾病登记/流调（仅登记数）", "db": "Orphanet/PubMed 等可核实",
        "guideline": "诊疗指南/专家共识/GeneReviews（二手值须溯源）", "web": "单篇研究/联网（附PMID）",
        "weak": "单中心小样本/弱源", "redflag": "高幻觉·必核（中国侧/死亡率/外推）",
        "na": "查过但确实没找到",
    }
    items = ""
    for k in ("official", "db", "guideline", "web", "weak", "redflag", "na"):
        items += '<div class="legend-item">{} <span class="legend-note">{}</span></div>'.format(
            chip(k), esc(notes[k]))
    return ('<section class="card"><h2>徽标说明（七档）</h2><div class="legend-grid">{}</div>'
            '<p class="note">可信度：🏛️官方 ＝ ✅库 ＝ 📘指南 ＞ 🔍web ＞ ⚠️ ＞ ❓N/A。🔴 是独立的"必核"标，'
            '可叠加在任何来源上（中国侧 / 死亡率 / 外推值即使来自官方也叠 🔴）。</p></section>').format(items)


def render_kv_section(title, sub_id, items, accent="#94a3b8", intro=None):
    if not items:
        return ""
    lis = ""
    for it in items:
        if isinstance(it, str):
            lis += "<li>{}</li>".format(esc(it))
            continue
        label = it.get("label") or it.get("field")
        text = it.get("text") or it.get("value") or ""
        body = ("<b>{}：</b>".format(esc(label)) if label else "") + esc(text)
        lis += "<li>{} {}</li>".format(body, chips(it.get("badge")))
    intro_html = '<p class="note">{}</p>'.format(esc(intro)) if intro else ""
    return ('<section class="card" id="{}" style="--accent:{}"><h2>{}</h2>{}'
            '<ul class="bullets">{}</ul></section>').format(sub_id, accent, esc(title), intro_html, lis)


def render_subtype_matrix(sm):
    if not sm or not sm.get("rows"):
        return ""
    cols = sm.get("columns") or ["亚型", "国际", "美国", "中国"]
    thead = "".join("<th>{}</th>".format(esc(c)) for c in cols)
    trs = ""
    for r in sm["rows"]:
        cells = r.get("cells") or []
        tds = '<th class="ind-name">{}</th>'.format(esc(r.get("subtype")))
        for c in cells:
            if not c:
                tds += '<td class="ind-cell"><span class="status st-off">—</span></td>'
                continue
            kind = esc(c.get("kind") or "")
            kindline = '<div class="ind-trial">{}</div>'.format(kind) if kind else ""
            tds += '<td class="ind-cell">{} {}{}</td>'.format(esc(c.get("value")), chips(c.get("badge")), kindline)
        trs += "<tr>{}</tr>".format(tds)
    note = esc(sm.get("note") or "携带频率 vs 临床患病率必须分列、绝不混算；外推值标 🔴。")
    return ('<section class="card"><h2>亚型矩阵（携带频率 vs 临床患病率分列）</h2>'
            '<table class="matrix"><thead><tr>{}</tr></thead><tbody>{}</tbody></table>'
            '<p class="note">{}</p></section>').format(thead, trs, note)


EPI_COLS = [
    ("year", "年份(采集/发表)"), ("sample_size", "样本量/base"), ("patient_type", "患者类型/纳入"),
    ("metric", "metric"), ("value", "数值+CI"), ("geo_level", "地理层级"),
    ("denominator", "分母"), ("case_definition", "病例定义"), ("study_design", "设计"),
]


def render_epi_table(tbl):
    rows = tbl.get("rows") or []
    if not rows:
        return ""
    thead = "".join("<th>{}</th>".format(esc(lbl)) for _, lbl in EPI_COLS) + "<th>来源+徽标</th>"
    trs = ""
    for r in rows:
        tds = ""
        for key, _ in EPI_COLS:
            cls = ' class="hl"' if key in ("year", "sample_size", "patient_type") else ""
            tds += "<td{}>{}</td>".format(cls, esc(r.get(key)))
        src = r.get("source") or {}
        if isinstance(src, dict):
            lab = esc(src.get("label") or "")
            url = src.get("url")
            lab_html = ('<a href="{}" target="_blank" rel="noopener">{}</a>'.format(esc(url), lab)
                        if url else lab)
            tds += "<td>{} {}</td>".format(lab_html, chips(src.get("badge")))
        else:
            tds += "<td>{}</td>".format(esc(src))
        trs += "<tr>{}</tr>".format(tds)
    conflict = tbl.get("conflict_note")
    conflict_html = ('<div class="conflict"><b>冲突解读：</b>{}</div>'.format(esc(conflict))
                     if conflict else "")
    return ('<section class="card"><h2>{} <span class="epi-tag">10 列口径 · 不取平均</span></h2>'
            '<div class="tablewrap"><table class="epi"><thead><tr>{}</tr></thead>'
            '<tbody>{}</tbody></table></div>{}'
            '<p class="note">前三列（年份/样本量/患者类型）是判断数字可比性的第一依据；数字差异多源于此。</p>'
            '</section>').format(esc(tbl.get("title") or "epi 对比表"), thead, trs, conflict_html)


def render_timeline(points):
    """Estimate-vs-year scatter/line: each point labelled with sample size + badge."""
    pts = [p for p in (points or []) if year_to_float(p.get("year"))]
    if not pts:
        return ""
    pts.sort(key=lambda p: year_to_float(p.get("year")))
    years = [year_to_float(p["year"]) for p in pts]
    ymin, ymax = min(years), max(years)
    span = max(ymax - ymin, 1)
    width, height = 880, 380
    # generous side padding so labels (which can sit on either side of a point) never clip
    pad_l, pad_r, pad_t, pad_b = 150, 150, 40, 56
    plot_w = width - pad_l - pad_r
    n = len(pts)
    parts = ['<svg viewBox="0 0 {} {}" width="100%" role="img" aria-label="估计值随年份">'.format(width, height)]
    # baseline
    parts.append('<line x1="{}" y1="{:.0f}" x2="{}" y2="{:.0f}" stroke="#cbd5e1" stroke-width="1.5"/>'.format(
        pad_l, height - pad_b, width - pad_r, height - pad_b))
    prev = None
    for i, p in enumerate(pts):
        x = pad_l + (plot_w * ((year_to_float(p["year"]) - ymin) / span)) if span else pad_l + plot_w / 2
        if n == 1:
            x = pad_l + plot_w / 2
        y = pad_t + 18 + (i % 4) * 70  # stagger vertically over 4 bands to avoid label collisions
        color = _badge_color(p.get("badge"))
        if prev is not None:
            parts.append('<line x1="{:.0f}" y1="{:.0f}" x2="{:.0f}" y2="{:.0f}" stroke="#e2e8f0" stroke-width="1.5"/>'.format(
                prev[0], prev[1], x, y))
        prev = (x, y)
        parts.append('<circle cx="{:.0f}" cy="{:.0f}" r="6" fill="{}" stroke="#fff" stroke-width="2"/>'.format(x, y, color))
        val = esc(p.get("value") or "")
        sample = esc(p.get("sample") or "")
        geo = esc(p.get("geo") or "")
        label = esc(p.get("label") or "")
        meta = " · ".join(b for b in (sample, geo) if b)
        # points in the right half put their labels on the LEFT (anchor=end) so nothing runs off-canvas
        right_side = x > (pad_l + plot_w * 0.5)
        lx = x - 9 if right_side else x + 9
        anchor = "end" if right_side else "start"
        parts.append('<text x="{:.0f}" y="{:.0f}" class="tl-v" style="text-anchor:{}">{}</text>'.format(lx, y - 2, anchor, val))
        if label:
            parts.append('<text x="{:.0f}" y="{:.0f}" class="tl-l" style="text-anchor:{}">{}</text>'.format(lx, y + 12, anchor, label[:26]))
        if meta:
            parts.append('<text x="{:.0f}" y="{:.0f}" class="tl-m" style="text-anchor:{}">{}</text>'.format(lx, y + 24, anchor, meta[:30]))
        # year axis label: clamp anchor at the extremes so first/last years never clip
        ax_anchor = "start" if i == 0 and n > 1 else ("end" if i == n - 1 and n > 1 else "middle")
        parts.append('<text x="{:.0f}" y="{:.0f}" class="ax-x" style="text-anchor:{}">{}</text>'.format(
            x, height - pad_b + 18, ax_anchor, esc(p.get("year"))))
    parts.append("</svg>")
    return ('<section class="card"><h2>估计值随年份（每点标样本量 + 徽标）</h2>'
            '<div class="chart">{}</div>'
            '<p class="note">点的颜色 = 来源徽标颜色（蓝=🏛️官方、绿=✅库、靛=📘指南、青=🔍web、琥珀=⚠️、红=🔴）。'
            '不同年份/样本的点**不连成趋势线解读**，仅为时间排列；外推/建模点标 🔴。</p></section>').format("".join(parts))


def render_checklist(items):
    items = items or []
    trs = ""
    for i, it in enumerate(items, 1):
        trs += ("<tr><td class='num'>{}</td><td>{}</td><td>{} {}</td><td>{}</td><td>{}</td></tr>".format(
            i, esc(it.get("field")), esc(it.get("value")), chips(it.get("badge")),
            esc(it.get("why")), esc(it.get("where"))))
    return ('<section class="card checklist"><h2>\U0001F534 需人工核对清单 '
            '<span class="ck-tag">本工具最关键产出</span></h2>'
            '<table class="ck-table"><thead><tr><th>#</th><th>字段</th><th>当前值 / 状态</th>'
            '<th>为什么要核</th><th>建议去哪个源核</th></tr></thead><tbody>{}</tbody></table></section>').format(trs)


def render_references(refs):
    if not refs:
        return ""
    lis = ""
    for r in refs:
        if isinstance(r, str):
            url, label, badge = r, r, None
        else:
            url = r.get("url") or ""
            label = r.get("label") or url
            badge = r.get("badge")
        href = '<a href="{u}" target="_blank" rel="noopener">{l}</a>'.format(u=esc(url), l=esc(label))
        urlspan = '<div class="ref-url">{}</div>'.format(esc(url)) if url else ""
        lis += "<li>{} {}{}</li>".format(href, chips(badge), urlspan)
    return ('<section class="card refs" id="references"><h2>参考来源（Reference list）</h2>'
            '<p class="note">下列为本报告所有引用链接（PMID/URL）的汇总；请优先以 🏛️官方 / ✅库 / 📘指南 溯源核对。</p>'
            '<ol class="reflist">{}</ol></section>').format(lis)


CSS = """
:root{
  --ink:#1e293b; --muted:#64748b; --line:#dde3ea; --soft:#eef2f7; --bg:#f4f6f9;
  --official:#2b4a6f; --db:#35637a; --guide:#4a5a82; --web:#5c748e; --weak:#8a7b5e; --red:#9d4b4b; --na:#94a3b8;
  --navy:#2b4a6f; --accent:#2b4a6f;
}
*{box-sizing:border-box;}
body{font-family:ui-sans-serif,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",Roboto,Helvetica,Arial,sans-serif;
  color:var(--ink); background:var(--bg); margin:0; padding:28px 18px; line-height:1.6; -webkit-font-smoothing:antialiased;}
.wrap{max-width:920px; margin:0 auto; display:flex; flex-direction:column; gap:18px;}
.hero{background:#fff; border:1px solid var(--line); border-top:3px solid var(--navy); border-radius:16px; padding:26px 30px; box-shadow:0 1px 2px rgba(16,24,40,.04);}
.hero-eyebrow{font-size:12.5px; font-weight:600; letter-spacing:.04em; color:#516074;}
h1{font-size:27px; line-height:1.2; margin:6px 0 2px; font-weight:700; color:#1c3553;}
.hero-sub{color:#3a4a5e; font-size:14px; margin-top:4px;}
.hero-meta{color:var(--muted); font-size:12.5px; margin-top:6px;}
.hero-meta code{background:var(--soft); padding:1px 6px; border-radius:5px; font-size:11.5px;}
.anchor{margin-top:8px; font-size:13px; color:#2b4a6f; background:#eef2f7; border:1px solid #ccd8e6; border-radius:8px; padding:7px 11px;}
.banner{margin-top:14px; padding:10px 14px; border-radius:10px; font-size:13.5px;}
.banner.amber{background:#eef2f7; border:1px solid #ccd8e6; color:#3a4a63;}
.banner.red{background:#f6f0f0; border:1px solid #dcc6c6; color:#7a4242; font-weight:600;}
.card{background:#fff; border:1px solid var(--line); border-radius:16px; padding:22px 26px; box-shadow:0 1px 2px rgba(16,24,40,.04);}
h2{font-size:17px; font-weight:700; color:#24435f; margin:0 0 14px; padding-left:11px; border-left:4px solid var(--accent); line-height:1.25;}
.idtable{width:100%; border-collapse:collapse; margin-top:16px; font-size:13.5px;}
.idtable td{border:1px solid var(--line); padding:8px 11px; vertical-align:top; overflow-wrap:anywhere;}
.idtable .k{width:190px; background:var(--soft); font-weight:600; color:#3a4a5e;}
.chip{display:inline-block; font-size:11px; font-weight:600; color:#fff; padding:2px 8px; border-radius:999px; margin:0 2px; vertical-align:middle; white-space:nowrap;}
.b-official{background:var(--official);} .b-db{background:var(--db);} .b-guide{background:var(--guide);} .b-web{background:var(--web);}
.b-weak{background:var(--weak);} .b-red{background:var(--red);} .b-na{background:var(--na);}
.legend-grid{display:grid; grid-template-columns:repeat(2,1fr); gap:9px 20px;}
.legend-item{font-size:13px;} .legend-note{color:var(--muted); margin-left:6px;}
.bullets{margin:2px 0; padding-left:20px;} .bullets li{margin:7px 0; font-size:14px;}
.note{color:var(--muted); font-size:12.5px; margin:12px 0 0;}
.matrix{width:100%; border-collapse:collapse; font-size:13.5px;}
.matrix th,.matrix td{border:1px solid var(--line); padding:9px 11px; text-align:left; vertical-align:top; overflow-wrap:anywhere;}
.matrix thead th{background:var(--soft);}
.ind-name{background:var(--soft); font-weight:600; width:30%;}
.status{display:inline-block; font-size:11.5px; font-weight:600; padding:2px 9px; border-radius:999px;}
.st-off{background:#eef2f7; color:#64748b;}
.ind-trial{font-size:11px; color:var(--muted); margin-top:4px;}
.tablewrap{overflow-x:auto;}
.epi{width:100%; border-collapse:collapse; font-size:12px; min-width:760px;}
.epi th,.epi td{border:1px solid var(--line); padding:7px 8px; text-align:left; vertical-align:top; overflow-wrap:anywhere;}
.epi thead th{background:var(--soft); font-size:11.5px; white-space:nowrap;}
.epi td.hl{background:#f7f9fb;}
.epi th:nth-child(-n+3){border-bottom:2px solid var(--navy);}
.epi-tag{font-size:11.5px; background:#eef2f7; color:#2b4a6f; border:1px solid #ccd8e6; padding:2px 9px; border-radius:999px; margin-left:8px; font-weight:600;}
.conflict{margin-top:12px; font-size:13px; background:#f4f6f9; border:1px solid #ccd6e2; border-radius:9px; padding:10px 13px; color:#3f4b5e;}
.chart{border:1px solid var(--line); border-radius:12px; padding:12px; background:#fff; overflow-x:auto;}
.tl-v{font-size:12px; fill:#1e293b; font-weight:700;}
.tl-l{font-size:10.5px; fill:#516074;}
.tl-m{font-size:10px; fill:#94a3b8;}
.ax-x{font-size:11px; fill:#516074; text-anchor:middle;}
.checklist{border:1.5px solid #d2c0c0; background:#faf7f7;}
.checklist h2{border-left-color:#9d4b4b; color:#7a4242;}
.ck-tag{font-size:12px; background:#9d4b4b; color:#fff; padding:2px 10px; border-radius:999px; margin-left:8px; font-weight:600;}
.ck-table{width:100%; border-collapse:collapse; font-size:13px;}
.ck-table th,.ck-table td{border:1px solid #e2d2d2; padding:9px 11px; text-align:left; vertical-align:top; overflow-wrap:anywhere;}
.ck-table thead th{background:#efe6e6; color:#7a4242;}
.ck-table .num{width:26px; text-align:center; color:#7a4242; font-weight:700;}
.refs .reflist{margin:6px 0 0; padding-left:22px;}
.refs .reflist li{margin:9px 0; font-size:13px;}
.refs a{color:#2b4a6f; text-decoration:none; font-weight:600;}
.refs a:hover{text-decoration:underline;}
.ref-url{font-size:11px; color:var(--muted); word-break:break-all; margin-top:1px;}
footer{color:var(--muted); font-size:12px; text-align:center; padding:4px 0 8px;}
@page{ size:A4; margin:12mm; }
@media print{
  body{background:#fff; padding:0;}
  .wrap{gap:14px; max-width:100%;}
  .hero,.card{box-shadow:none; break-inside:avoid;}
  .legend-grid{grid-template-columns:1fr 1fr;}
  .checklist{break-inside:avoid;}
}
"""


def build_html(data):
    meta = data.get("meta", {})
    title = esc(meta.get("disease") or meta.get("name") or "罕见病流行病学报告")
    parts = [render_header(meta, data.get("identity")), render_legend()]
    parts.append(render_kv_section("共识锚点（病例定义 / 诊断标准 / 亚型分类 \U0001F4D8）", "anchor",
                                   data.get("consensus_anchor"), "#2b4a6f",
                                   intro="指南/共识对病例定义与亚型分类最高权威；其 epi 数字为二手值，须溯原始研究。"))
    parts.append(render_subtype_matrix(data.get("subtype_matrix")))
    for tbl in (data.get("epi_tables") or []):
        parts.append(render_epi_table(tbl))
    parts.append(render_timeline(data.get("timeline")))
    parts.append(render_kv_section("死亡率（整项 \U0001F534：罕见病死亡登记普遍不全）", "mortality",
                                   data.get("mortality"), "#9d4b4b"))
    parts.append(render_checklist(data.get("checklist")))
    parts.append(render_references(data.get("references")))
    body = "\n".join(p for p in parts if p)
    foot = esc(meta.get("footer") or
               "rare-disease-epi · 仅供研究情报参考，非医疗建议 · 标 \U0001F534 项请以官方/指南/原始研究人工复核为准。")
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>罕见病流行病学报告 · {title}</title>
<style>{css}</style>
</head><body>
<div class="wrap">
{body}
<footer>{foot}</footer>
</div>
</body></html>""".format(title=title, css=CSS, body=body, foot=foot)


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Render a rare-disease-epi visual HTML report from a JSON data file.")
    ap.add_argument("data", help="path to the JSON data file (schema: templates/report_schema.json)")
    ap.add_argument("-o", "--out", default=None, help="output .html path (default: alongside the data file)")
    args = ap.parse_args(argv)
    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)
    html_str = build_html(data)
    out = args.out or (args.data.rsplit(".", 1)[0] + ".html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_str)
    print("Wrote {} ({:.1f} KB)".format(out, len(html_str.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
