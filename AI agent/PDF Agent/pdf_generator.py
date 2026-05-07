"""
pdf_generator.py — Premium Playwright HTML→PDF Engine v3
=========================================================
Architecture:
  1. Markdown text (from LLM) → Python-Markdown → HTML body
  2. HTML body injected into a full-page CSS-styled template
  3. Playwright (Chromium headless) renders → PDF

Result: browser-quality PDF with full CSS3, system CJK fonts,
        colored sections, financial tables, gradients, shadows.
"""
import os, re, sys, uuid, subprocess, json, math, html as _html, markdown as _md_lib
from datetime import datetime

# Process isolation solves Event Loop bugs under Uvicorn/Windows.

# ─── Paths ────────────────────────────────────────────────────────────────────
import tempfile
import gridfs
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config_loader import cfg

mongo_client = MongoClient(cfg.mongo_uri)
db = mongo_client[cfg.mongo_database]
fs = gridfs.GridFS(db)

PDF_DIR = tempfile.gettempdir()

# ─── Accent colour per document type ─────────────────────────────────────────
_TYPE_THEME = {
    "financial"   : {"accent": "#1d4ed8", "accent_lt": "#dbeafe", "badge": "📊 Financial Report"},
    "annual_report": {"accent": "#b45309", "accent_lt": "#fef3c7", "badge": "📋 Annual Report"},
    "academic"    : {"accent": "#059669", "accent_lt": "#d1fae5", "badge": "🎓 Academic Analysis"},
    "legal"       : {"accent": "#7c3aed", "accent_lt": "#ede9fe", "badge": "⚖️ Legal Document"},
    "medical"     : {"accent": "#0891b2", "accent_lt": "#cffafe", "badge": "🏥 Medical Report"},
    "business"    : {"accent": "#ea580c", "accent_lt": "#ffedd5", "badge": "💼 Business Analysis"},
    "general"     : {"accent": "#1d4ed8", "accent_lt": "#dbeafe", "badge": "📄 Document Analysis"},
}

def _theme(doc_type: str) -> dict:
    return _TYPE_THEME.get(doc_type, _TYPE_THEME["general"])

# ─── Full HTML Template ───────────────────────────────────────────────────────
_HTML_TEMPLATE_COLOR = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
/* ── Reset & Fonts ─────────────────────────────────── */
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, 
               "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  font-size: 10.5pt;
  color: #1e293b;
  line-height: 1.65;
  background: #fff;
}}

/* ── Fixed Header (every page) ─────────────────────── */
.page-header {{
  position: fixed; top: 0; left: 0; right: 0;
  height: 1.4cm;
  background: #0f2044;
  display: flex; align-items: center;
  justify-content: space-between;
  padding: 0 1.8cm;
  z-index: 1000;
}}
.page-header .ph-title {{
  font-size: 8pt; font-weight: 600;
  color: #93c5fd; letter-spacing: 0.03em;
  white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; max-width: 75%;
}}
.page-header .ph-badge {{
  font-size: 7.5pt; color: {accent};
  background: {accent_lt}; padding: 2px 8px;
  border-radius: 20px; white-space: nowrap;
  font-weight: 600;
}}
.gold-rule {{
  position: fixed; top: 1.4cm; left: 0; right: 0;
  height: 3px; background: {accent};
  z-index: 1000;
}}

/* ── Fixed Footer (every page) ─────────────────────── */
.page-footer {{
  position: fixed; bottom: 0; left: 0; right: 0;
  height: 1.1cm;
  background: #f0f4ff;
  border-top: 2px solid {accent};
  display: flex; align-items: center;
  justify-content: space-between;
  padding: 0 1.8cm;
  z-index: 1000;
}}
.page-footer span {{
  font-size: 7.5pt; color: #64748b;
}}

/* ── Cover Page ─────────────────────────────────────── */
.cover {{
  min-height: 100vh;
  display: flex; flex-direction: column;
  page-break-after: always;
}}
.cover-hero {{
  background: linear-gradient(150deg, #0f2044 0%, #1a3060 55%, #0f2044 100%);
  flex: 1;
  padding: 3.5cm 2cm 2.5cm 2cm;
  display: flex; flex-direction: column;
  justify-content: flex-end;
  position: relative;
  overflow: hidden;
}}
.cover-hero::before {{
  content: '';
  position: absolute; top: -80px; right: -100px;
  width: 420px; height: 420px;
  background: radial-gradient(circle, {accent}22 0%, transparent 65%);
  border-radius: 50%;
}}
.cover-eyebrow {{
  font-size: 8pt; letter-spacing: 3px; font-weight: 700;
  color: {accent}; opacity: 0.9;
  text-transform: uppercase; margin-bottom: 16px;
}}
.cover-title {{
  font-size: 26pt; font-weight: 700; color: #fff;
  line-height: 1.2; margin-bottom: 16px;
  max-width: 88%;
}}
.cover-sub {{
  font-size: 11pt; color: #93c5fd;
  line-height: 1.5; max-width: 75%;
}}
.cover-accent-bar {{
  height: 5px;
  background: linear-gradient(90deg, {accent} 0%, {accent}88 60%, transparent 100%);
  margin: 20px 0 0 0;
}}
.cover-note {{
  background: #f8fafc;
  padding: 14px 2cm;
  font-size: 9pt;
  color: #475569;
  font-weight: 500;
  letter-spacing: 0.02em;
}}
.cover-disclaimer {{
  background: {accent};
  padding: 10px 2cm;
  font-size: 8pt; color: #fff; opacity: 0.92;
}}

/* ── Main Content ───────────────────────────────────── */
.content {{
  margin-top: 1.8cm;   /* clear fixed header */
  margin-bottom: 1.4cm; /* clear fixed footer */
  padding: 0.25cm 0;
}}

/* ── Headings ───────────────────────────────────────── */
h1 {{
  font-size: 17pt; color: #0f2044; font-weight: 700;
  margin: 22pt 0 8pt 0;
  padding-bottom: 6px;
  border-bottom: 3px solid {accent};
}}
h2 {{
  font-size: 12.5pt; font-weight: 700; color: #fff;
  background: #0f2044;
  margin: 18pt 0 6pt 0;
  padding: 9px 14px;
  border-left: 5px solid {accent};
  page-break-after: avoid;
}}
h3 {{
  font-size: 11pt; font-weight: 700; color: #0f2044;
  margin: 14pt 0 5pt 0;
  padding: 6px 12px;
  background: {accent_lt};
  border-left: 4px solid {accent};
  page-break-after: avoid;
}}
h4 {{
  font-size: 10.5pt; font-weight: 700; color: #334155;
  margin: 10pt 0 4pt 0;
}}

/* ── Paragraphs & Text ──────────────────────────────── */
p {{
  margin: 0 0 8pt 0;
  text-align: justify;
  hyphens: auto;
}}
strong {{ color: #0f2044; }}
em {{ color: #475569; }}
a {{ color: {accent}; text-decoration: underline; }}

/* ── Tables ─────────────────────────────────────────── */
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 10pt 0 14pt 0;
  font-size: 9pt;
  table-layout: auto;
  page-break-inside: auto;
  break-inside: auto;
  box-shadow: 0 1px 4px #0f204422;
}}
thead {{ display: table-header-group; }}
tfoot {{ display: table-footer-group; }}
tr {{ page-break-inside: avoid; break-inside: avoid; }}
thead tr {{
  background: #0f2044 !important;
  color: #fff !important;
}}
thead th {{
  padding: 9px 10px;
  font-weight: 700;
  text-align: left;
  border-bottom: 3px solid {accent};
  font-size: 9pt;
  letter-spacing: 0.02em;
}}
th, td {{
  overflow-wrap: anywhere;
  word-break: normal;
}}
tbody tr:nth-child(even)  {{ background: {accent_lt}; }}
tbody tr:nth-child(odd)   {{ background: #fff; }}
tbody tr:hover            {{ background: #f0f4ff; }}
tbody td {{
  padding: 7px 10px;
  border-bottom: 1px solid #e2e8f0;
  vertical-align: middle;
}}
/* Right-align cells that look numeric */
tbody td:not(:first-child) {{
  text-align: right;
}}
/* But if cell contains mostly text, override */
tbody td[data-text="true"] {{
  text-align: left;
}}
/* Positive/negative colouring done in JS below */
.num-pos {{ color: #059669; font-weight: 600; }}
.num-neg {{ color: #dc2626; font-weight: 600; }}

/* ── Charts ────────────────────────────────────────── */
.chart-card {{
  margin: 14pt 0 18pt 0;
  padding: 12pt 14pt;
  border: 1px solid #dbe4f0;
  border-left: 4px solid {accent};
  border-radius: 6px;
  background: #fff;
  page-break-inside: avoid;
  break-inside: avoid;
}}
.chart-title {{
  font-size: 10.5pt;
  font-weight: 700;
  color: #0f2044;
  margin-bottom: 8pt;
}}
.chart-card svg {{
  width: 100%;
  height: auto;
  display: block;
}}

/* ── Lists ──────────────────────────────────────────── */
ul {{
  margin: 4pt 0 8pt 18pt;
  list-style: none;
}}
ul li {{ margin: 3pt 0; position: relative; padding-left: 14px; }}
ul li::before {{
  content: '▸';
  color: {accent};
  position: absolute; left: 0;
  font-weight: 700;
}}
ol {{
  margin: 4pt 0 8pt 20pt;
}}
ol li {{ margin: 3pt 0; padding-left: 4px; }}
ol li::marker {{ color: {accent}; font-weight: 700; }}

/* ── Blockquote ─────────────────────────────────────── */
blockquote {{
  margin: 8pt 0;
  padding: 10px 16px;
  background: #fef3c7;
  border-left: 5px solid #b45309;
  color: #78350f;
  font-style: italic;
  border-radius: 0 4px 4px 0;
}}

/* ── Code ───────────────────────────────────────────── */
pre {{
  background: #0f2044;
  color: #e2e8f0;
  padding: 14px 16px;
  border-radius: 6px;
  font-size: 8.5pt;
  overflow-x: auto;
  margin: 8pt 0;
  border-left: 4px solid {accent};
}}
code {{
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 8.5pt;
  background: #f1f5f9;
  color: #0f2044;
  padding: 1px 5px;
  border-radius: 3px;
}}
pre code {{
  background: transparent;
  color: #e2e8f0;
  padding: 0;
}}

/* ── Horizontal Rule ────────────────────────────────── */
hr {{
  border: none;
  border-top: 1.5px solid {accent}44;
  margin: 14pt 0;
}}

/* ── Page Break ─────────────────────────────────────── */
.page-break {{ page-break-after: always; }}
@page {{
  size: A4;
  margin: 2cm 1.25cm 1.55cm 1.25cm;
}}
@media print {{
  .no-print {{ display: none !important; }}
}}
</style>
</head>
<body>

<!-- Fixed Header -->
<div class="page-header">
  <span class="ph-title">{title}</span>
  <span class="ph-badge">{badge}</span>
</div>
<div class="gold-rule"></div>

<!-- Fixed Footer -->
<div class="page-footer">
  <span>Confidential Report</span>
  <span>{now}</span>
</div>

<!-- ── Cover Page ── -->
<div class="cover">
  <div class="cover-hero">
    <div class="cover-eyebrow">DOCUMENT ANALYSIS</div>
    <div class="cover-title">{title}</div>
    <div class="cover-sub">{report_type}</div>
    <div class="cover-accent-bar"></div>
  </div>
  <div class="cover-note">
    Pepper AI Agent • {now}
  </div>
</div>

<!-- ── Page Break After Cover ── -->
<div class="content">
{body}
</div>

<script>
// Auto colour numeric cells green/red
document.querySelectorAll('tbody td').forEach(td => {{
  const txt = td.textContent.trim();
  // If starts with + or is positive % change
  if (/^\+|↑|▲/.test(txt)) {{
    td.classList.add('num-pos');
  }}
  // If starts with - or negative
  else if (/^-[\d]|↓|▼|\(\d/.test(txt)) {{
    td.classList.add('num-neg');
  }}
  // If first column (label), left-align
  if (td.cellIndex === 0) {{
    td.style.textAlign = 'left';
    td.style.fontWeight = '500';
  }}
}});
</script>
</body>
</html>
"""

_HTML_TEMPLATE_BW = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
/* ── Reset & Fonts ─────────────────────────────────── */
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{
  font-family: "Palatino Linotype", "Book Antiqua", Palatino, "Times New Roman", Times, "Songti SC", "SimSun", serif;
  font-size: 10.5pt;
  color: #000;
  line-height: 1.6;
  background: #fff;
}}

/* ── Fixed Header (every page) ─────────────────────── */
.page-header {{
  position: fixed; top: 0; left: 0; right: 0;
  height: 1.4cm;
  background: #fff;
  display: flex; align-items: center;
  justify-content: space-between;
  padding: 0 1.8cm;
  z-index: 1000;
}}
.page-header .ph-title {{
  font-size: 8pt; font-weight: 600;
  color: #000; letter-spacing: 0.03em;
  white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; max-width: 75%;
}}
.page-header .ph-badge {{
  font-size: 7.5pt; color: #000;
  background: transparent; padding: 2px 0;
  border-bottom: 1px solid #000;
  white-space: nowrap;
  font-weight: 600;
}}
.gold-rule {{
  position: fixed; top: 1.4cm; left: 0; right: 0;
  height: 1px; background: #000;
  z-index: 1000;
}}

/* ── Fixed Footer (every page) ─────────────────────── */
.page-footer {{
  position: fixed; bottom: 0; left: 0; right: 0;
  height: 1.1cm;
  background: #fff;
  border-top: 1px solid #000;
  display: flex; align-items: center;
  justify-content: space-between;
  padding: 0 1.8cm;
  z-index: 1000;
}}
.page-footer span {{
  font-size: 7.5pt; color: #64748b;
}}

/* ── Cover Page ─────────────────────────────────────── */
.cover {{
  min-height: 100vh;
  display: flex; flex-direction: column;
  page-break-after: always;
}}
.cover-hero {{
  background: #fff;
  flex: 1;
  padding: 3.5cm 2cm 2.5cm 2cm;
  display: flex; flex-direction: column;
  justify-content: flex-end;
  position: relative;
  overflow: hidden;
}}
.cover-hero::before {{
  content: '';
  position: absolute; top: -80px; right: -100px;
  width: 420px; height: 420px;
  display: none; /* Removed colored radial gradient */
}}
.cover-eyebrow {{
  font-size: 8pt; letter-spacing: 3px; font-weight: 700;
  color: #000; opacity: 0.9;
  border-bottom: 1px solid #000;
  display: inline-block;
  padding-bottom: 4px;
  text-transform: uppercase; margin-bottom: 24px;
}}
.cover-title {{
  font-size: 24pt; font-weight: 700; color: #000;
  line-height: 1.2; margin-bottom: 16px;
  max-width: 88%;
}}
.cover-sub {{
  font-size: 11pt; color: #555;
  line-height: 1.5; max-width: 75%;
}}
.cover-accent-bar {{
  height: 2px;
  background: #000;
  margin: 20px 0 0 0;
}}
.cover-note {{
  background: #f8fafc;
  padding: 14px 2cm;
  font-size: 9pt;
  color: #475569;
  font-weight: 500;
  letter-spacing: 0.02em;
}}
.cover-disclaimer {{
  background: #000;
  padding: 10px 2cm;
  font-size: 8pt; color: #fff; opacity: 1;
}}

/* ── Main Content ───────────────────────────────────── */
.content {{
  margin-top: 1.8cm;   /* clear fixed header */
  margin-bottom: 1.4cm; /* clear fixed footer */
  padding: 0.25cm 0;
}}

/* ── Headings ───────────────────────────────────────── */
h1 {{
  font-size: 16pt; color: #000; font-weight: 700;
  margin: 20pt 0 10pt 0;
  text-align: center;
  text-transform: uppercase;
}}
h2 {{
  font-size: 13pt; font-weight: 700; color: #000;
  margin: 18pt 0 8pt 0;
  border-bottom: 1px solid #000;
  padding-bottom: 3px;
  page-break-after: avoid;
}}
h3 {{
  font-size: 11pt; font-weight: 700; color: #000;
  margin: 14pt 0 6pt 0;
  page-break-after: avoid;
}}
h4 {{
  font-size: 10.5pt; font-weight: 700; color: #000;
  margin: 10pt 0 4pt 0;
  font-style: italic;
}}

/* ── Paragraphs & Text ──────────────────────────────── */
p {{
  margin: 0 0 8pt 0;
  text-align: justify;
  hyphens: auto;
  line-height: 1.5;
}}
strong {{ color: #000; font-weight: 700; }}
em {{ color: #000; font-style: italic; }}
a {{ color: #000; text-decoration: none; border-bottom: 1px solid #000; }}

/* ── Tables ─────────────────────────────────────────── */
/* LaTeX / High-End Financial Style Tables */
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 14pt 0 18pt 0;
  font-size: 9pt;
  table-layout: auto;
  page-break-inside: auto;
  break-inside: auto;
  border-top: 2px solid #000;
  border-bottom: 2px solid #000;
}}
thead {{ display: table-header-group; }}
tfoot {{ display: table-footer-group; }}
tr {{ page-break-inside: avoid; break-inside: avoid; }}
thead tr {{
  background: transparent !important;
  color: #000 !important;
}}
thead th {{
  padding: 8px 6px;
  font-weight: 700;
  text-align: left;
  border-bottom: 1px solid #000;
  font-size: 9pt;
  vertical-align: bottom;
}}
th, td {{
  overflow-wrap: anywhere;
  word-break: normal;
}}
tbody tr {{ background: transparent !important; }}
tbody td {{
  padding: 6px 6px;
  border-bottom: none;
  vertical-align: top;
}}
/* Right-align cells that look numeric */
tbody td:not(:first-child) {{
  text-align: right;
}}
/* But if cell contains mostly text, override */
tbody td[data-text="true"] {{
  text-align: left;
}}
/* No positive/negative colours in strict monochrome */
.num-pos {{ color: #000; }}
.num-neg {{ color: #000; }}

/* ── Charts ────────────────────────────────────────── */
.chart-card {{
  margin: 14pt 0 18pt 0;
  padding: 12pt 14pt;
  border: 1px solid #000;
  border-left: 4px solid #000;
  border-radius: 4px;
  background: #fff;
  page-break-inside: avoid;
  break-inside: avoid;
}}
.chart-title {{
  font-size: 10.5pt;
  font-weight: 700;
  color: #000;
  margin-bottom: 8pt;
}}
.chart-card svg {{
  width: 100%;
  height: auto;
  display: block;
}}

/* ── Lists ──────────────────────────────────────────── */
ul {{
  margin: 4pt 0 8pt 18pt;
  list-style: none;
}}
ul li {{ margin: 3pt 0; position: relative; padding-left: 14px; }}
ul li::before {{
  content: '▸';
  color: #000;
  position: absolute; left: 0;
  font-weight: 700;
}}
ol {{
  margin: 4pt 0 8pt 20pt;
}}
ol li {{ margin: 3pt 0; padding-left: 4px; }}
ol li::marker {{ color: #000; font-weight: 700; }}

/* ── Blockquote ─────────────────────────────────────── */
blockquote {{
  margin: 8pt 0;
  padding: 10px 16px;
  background: transparent;
  border-left: 3px solid #000;
  color: #000;
  font-style: italic;
  border-radius: 0 4px 4px 0;
}}

/* ── Code ───────────────────────────────────────────── */
pre {{
  background: #fff;
  color: #000;
  padding: 14px 16px;
  border-radius: 6px;
  font-size: 8.5pt;
  overflow-x: auto;
  margin: 8pt 0;
  border: 1px solid #000;
}}
code {{
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 8.5pt;
  background: #fff;
  color: #000;
  padding: 1px 5px;
  border-radius: 3px;
}}
pre code {{
  background: transparent;
  color: #000;
  padding: 0;
}}

/* ── Horizontal Rule ────────────────────────────────── */
hr {{
  border: none;
  border-top: 1.5px solid {accent}44;
  margin: 14pt 0;
}}

/* ── Page Break ─────────────────────────────────────── */
.page-break {{ page-break-after: always; }}
@page {{
  size: A4;
  margin: 2cm 1.25cm 1.55cm 1.25cm;
}}
@media print {{
  .no-print {{ display: none !important; }}
}}
</style>
</head>
<body>

<!-- Fixed Header -->
<div class="page-header">
  <span class="ph-title">{title}</span>
  <span class="ph-badge">{badge}</span>
</div>
<div class="gold-rule"></div>

<!-- Fixed Footer -->
<div class="page-footer">
  <span>Confidential Report</span>
  <span>{now}</span>
</div>

<!-- ── Cover Page ── -->
<div class="cover">
  <div class="cover-hero">
    <div class="cover-eyebrow">DOCUMENT ANALYSIS</div>
    <div class="cover-title">{title}</div>
    <div class="cover-sub">{report_type}</div>
    <div class="cover-accent-bar"></div>
  </div>
  <div class="cover-note">
    Pepper AI Agent • {now}
  </div>
</div>

<!-- ── Page Break After Cover ── -->
<div class="content">
{body}
</div>

<script>
// Auto colour numeric cells green/red
document.querySelectorAll('tbody td').forEach(td => {{
  const txt = td.textContent.trim();
  // If starts with + or is positive % change
  if (/^\+|↑|▲/.test(txt)) {{
    td.classList.add('num-pos');
  }}
  // If starts with - or negative
  else if (/^-[\d]|↓|▼|\(\d/.test(txt)) {{
    td.classList.add('num-neg');
  }}
  // If first column (label), left-align
  if (td.cellIndex === 0) {{
    td.style.textAlign = 'left';
    td.style.fontWeight = '500';
  }}
}});
</script>
</body>
</html>
"""

# ─── LaTeX math symbols → Unicode (common model output patterns) ─────────────
_LATEX_SYMBOLS = [
    (r'\$\\checkmark\$', '✓'), (r'\$\\check\$', '✓'),
    (r'\$\\times\$',     '✗'), (r'\$\\cross\$',  '✗'),
    (r'\$\\uparrow\$',   '↑'), (r'\$\\Uparrow\$', '⬆'),
    (r'\$\\downarrow\$', '↓'), (r'\$\\Downarrow\$','⬇'),
    (r'\$\\rightarrow\$','→'), (r'\$\\leftarrow\$', '←'),
    (r'\$\\leftrightarrow\$','↔'),
    (r'\$\\approx\$',    '≈'), (r'\$\\neq\$',    '≠'),
    (r'\$\\leq\$',       '≤'), (r'\$\\geq\$',    '≥'),
    (r'\$\\pm\$',        '±'), (r'\$\\infty\$',  '∞'),
    (r'\$\\Delta\$',     'Δ'), (r'\$\\delta\$',  'δ'),
    (r'\$\\sigma\$',     'σ'), (r'\$\\alpha\$',  'α'),
    (r'\$\\beta\$',      'β'), (r'\$\\gamma\$',  'γ'),
    (r'\$\\%\$',         '%'),
    # bare (non-dollar-wrapped) variants the model sometimes emits
    (r'\\checkmark',     '✓'), (r'\\times',      '✗'),
    (r'\\uparrow',       '↑'), (r'\\downarrow',  '↓'),
    (r'\\rightarrow',    '→'), (r'\\leftarrow',  '←'),
    (r'\\approx',        '≈'), (r'\\neq',        '≠'),
    (r'\\leq',           '≤'), (r'\\geq',        '≥'),
    (r'\\pm',            '±'),
]

def _convert_latex_symbols(text: str) -> str:
    for pattern, replacement in _LATEX_SYMBOLS:
        text = re.sub(pattern, replacement, text)
    # Strip any remaining inline math delimiters: $...$  (single-char or short expression)
    text = re.sub(r'\$([^$\n]{1,40})\$', r'\1', text)
    return text

def _clean_residual_placeholders(text: str) -> str:
    """Replace bracket placeholders the model sometimes leaves in tables with N/A."""
    # e.g.  [Data from P&L]  [%]  [Value]  [Amount]  [X]  [Name]
    text = re.sub(r'\[Data\s+from\s+[^\]]+\]', 'N/A', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*%\s*\]', 'N/A', text)
    text = re.sub(r'\[\s*(?:Value|Amount|Name|X|Formula|Number|Total|Data|Insert)\s*\]', 'N/A', text, flags=re.IGNORECASE)
    # Also replace bracket formulas like [Aset Semasa / Liabiliti Semasa] with N/A
    text = re.sub(r'\[[^\]]{3,60}/[^\]]{3,60}\]', 'N/A', text)
    return text

_CHART_COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#64748b"]

def _chart_num(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value or ""))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".", "-.") else 0.0
    except ValueError:
        return 0.0

def _svg_text(text: str) -> str:
    return _html.escape(str(text or ""), quote=False)

def _normalise_chart_spec(spec: dict) -> dict:
    chart_type = str(spec.get("type") or "bar").lower().strip()
    labels = [str(x) for x in (spec.get("labels") or [])]
    raw_series = spec.get("series") or []
    if not raw_series and spec.get("values") is not None:
        raw_series = [{"name": spec.get("name") or "Value", "values": spec.get("values")}]
    series = []
    for item in raw_series:
        if not isinstance(item, dict):
            continue
        values = item.get("values") or []
        if not isinstance(values, list):
            values = [values]
        series.append({
            "name": str(item.get("name") or "Value"),
            "values": [_chart_num(v) for v in values],
        })
    if not labels and series:
        labels = [str(i + 1) for i in range(len(series[0]["values"]))]
    return {
        "type": chart_type if chart_type in {"bar", "pie", "line"} else "bar",
        "title": str(spec.get("title") or "Chart"),
        "labels": labels,
        "series": series,
    }

def _render_bar_chart(chart: dict) -> str:
    labels, series = chart["labels"], chart["series"][:4]
    if not labels or not series:
        return ""
    width, height = 760, 360
    left, right, top, bottom = 58, 24, 24, 58
    plot_w, plot_h = width - left - right, height - top - bottom
    max_val = max([0.0] + [v for s in series for v in s["values"]])
    max_val = max_val or 1.0
    group_w = plot_w / max(len(labels), 1)
    bar_w = min(34, group_w / max(len(series), 1) * 0.68)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_svg_text(chart["title"])}">']
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#94a3b8" stroke-width="1"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#94a3b8" stroke-width="1"/>')
    for gi, label in enumerate(labels):
        base_x = left + gi * group_w + group_w / 2
        for si, serie in enumerate(series):
            value = serie["values"][gi] if gi < len(serie["values"]) else 0
            bar_h = max(0, value / max_val * plot_h)
            x = base_x - (len(series) * bar_w) / 2 + si * bar_w
            y = top + plot_h - bar_h
            color = _CHART_COLORS[si % len(_CHART_COLORS)]
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 2:.1f}" height="{bar_h:.1f}" rx="3" fill="{color}"/>')
        parts.append(f'<text x="{base_x:.1f}" y="{height - 31}" font-size="12" text-anchor="middle" fill="#334155">{_svg_text(label[:14])}</text>')
    for si, serie in enumerate(series):
        lx = left + si * 150
        ly = height - 10
        color = _CHART_COLORS[si % len(_CHART_COLORS)]
        parts.append(f'<rect x="{lx}" y="{ly - 10}" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{lx + 15}" y="{ly}" font-size="12" fill="#334155">{_svg_text(serie["name"][:20])}</text>')
    parts.append(f'<text x="{left - 8}" y="{top + 6}" font-size="11" text-anchor="end" fill="#64748b">{max_val:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)

def _render_line_chart(chart: dict) -> str:
    labels, series = chart["labels"], chart["series"][:4]
    if not labels or not series:
        return ""
    width, height = 760, 360
    left, right, top, bottom = 58, 24, 24, 58
    plot_w, plot_h = width - left - right, height - top - bottom
    values = [v for s in series for v in s["values"]]
    min_val, max_val = min(values or [0]), max(values or [1])
    if min_val == max_val:
        min_val = 0
        max_val = max_val or 1
    def pt(idx, value):
        x = left + (idx / max(len(labels) - 1, 1)) * plot_w
        y = top + plot_h - ((value - min_val) / (max_val - min_val)) * plot_h
        return x, y
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_svg_text(chart["title"])}">']
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#94a3b8" stroke-width="1"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#94a3b8" stroke-width="1"/>')
    for si, serie in enumerate(series):
        coords = [pt(i, serie["values"][i] if i < len(serie["values"]) else 0) for i in range(len(labels))]
        color = _CHART_COLORS[si % len(_CHART_COLORS)]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y in coords:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>')
        lx = left + si * 150
        parts.append(f'<rect x="{lx}" y="{height - 20}" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{lx + 15}" y="{height - 10}" font-size="12" fill="#334155">{_svg_text(serie["name"][:20])}</text>')
    for i, label in enumerate(labels):
        x, _ = pt(i, min_val)
        parts.append(f'<text x="{x:.1f}" y="{height - 31}" font-size="12" text-anchor="middle" fill="#334155">{_svg_text(label[:14])}</text>')
    parts.append(f'<text x="{left - 8}" y="{top + 6}" font-size="11" text-anchor="end" fill="#64748b">{max_val:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)

def _pie_slice_path(cx, cy, r, start_angle, end_angle):
    x1, y1 = cx + r * math.cos(start_angle), cy + r * math.sin(start_angle)
    x2, y2 = cx + r * math.cos(end_angle), cy + r * math.sin(end_angle)
    large = 1 if end_angle - start_angle > math.pi else 0
    return f"M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {large} 1 {x2:.1f} {y2:.1f} Z"

def _render_pie_chart(chart: dict) -> str:
    labels = chart["labels"]
    values = chart["series"][0]["values"] if chart["series"] else []
    if not labels or not values:
        return ""
    pairs = [(labels[i], max(0, values[i] if i < len(values) else 0)) for i in range(len(labels))]
    total = sum(v for _, v in pairs) or 1
    width, height = 760, 340
    cx, cy, r = 210, 165, 115
    angle = -math.pi / 2
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_svg_text(chart["title"])}">']
    for i, (label, value) in enumerate(pairs[:10]):
        span = (value / total) * 2 * math.pi
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        parts.append(f'<path d="{_pie_slice_path(cx, cy, r, angle, angle + span)}" fill="{color}" stroke="#fff" stroke-width="2"/>')
        pct = value / total * 100
        ly = 50 + i * 24
        parts.append(f'<rect x="390" y="{ly - 11}" width="12" height="12" fill="{color}"/>')
        parts.append(f'<text x="410" y="{ly}" font-size="13" fill="#334155">{_svg_text(label[:28])} ({pct:.1f}%)</text>')
        angle += span
    parts.append("</svg>")
    return "".join(parts)

def _render_chart_spec(spec: dict) -> str:
    chart = _normalise_chart_spec(spec)
    if chart["type"] == "pie":
        svg = _render_pie_chart(chart)
    elif chart["type"] == "line":
        svg = _render_line_chart(chart)
    else:
        svg = _render_bar_chart(chart)
    if not svg:
        return ""
    return (
        '<figure class="chart-card">'
        f'<figcaption class="chart-title">{_svg_text(chart["title"])}</figcaption>'
        f'{svg}'
        '</figure>'
    )

def _render_chart_blocks(md_text: str) -> str:
    def repl(match):
        raw = match.group(1).strip()
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        return "\n\n" + _render_chart_spec(spec) + "\n\n"
    return re.sub(r"```chart\s*(\{.*?\})\s*```", repl, md_text, flags=re.DOTALL | re.IGNORECASE)

# ─── Markdown → HTML (body only) ─────────────────────────────────────────────
def _md_to_html_body(md_text: str) -> str:
    # Strip <think> blocks
    md_text = re.sub(r"<think>.*?</think>", "", md_text, flags=re.DOTALL).strip()
    # Convert supported chart code fences into inline SVG before Markdown parsing.
    md_text = _render_chart_blocks(md_text)
    # Convert LaTeX math symbols to Unicode before Markdown parsing
    md_text = _convert_latex_symbols(md_text)
    # Replace residual bracket placeholders
    md_text = _clean_residual_placeholders(md_text)
    html = _md_lib.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "attr_list", "smarty", "toc"],
        extension_configs={"nl2br": {}},
    )
    return html

async def markdown_to_pdf(markdown_text: str, doc_type: str = "general", is_template: bool = False) -> tuple:
    """
    Convert a Markdown string to a premium PDF.
    Returns (absolute_path, filename).
    """
    now_str     = datetime.now().strftime("%Y-%m-%d  %H:%M")
    now_compact = datetime.now().strftime("%Y%m%d")

    # Extract doc title from first heading for smart naming (H1, H2, etc.)
    # Ignore <think> blocks by searching globally
    m = re.search(r"^#+\s+(.+)", markdown_text, re.MULTILINE)
    raw_title = m.group(1).strip() if m else ""
    
    # Build a clean filename from the title
    if raw_title:
        # Remove markdown formatting artifacts
        clean = re.sub(r'[*_`#\[\]]', '', raw_title)
        clean = re.sub(r'[—–]', '-', clean)
        # Keep only safe filename characters
        clean = re.sub(r'[^\w\s\-]', '', clean).strip()
        # Replace spaces with underscores, collapse multiples
        clean = re.sub(r'\s+', '_', clean)
        # Truncate to reasonable length
        if len(clean) > 60:
            clean = clean[:60].rsplit('_', 1)[0]
        filename = f"{clean}_{now_compact}.pdf" if clean else f"PepperReport_{uuid.uuid4().hex[:8]}.pdf"
    else:
        filename = f"PepperReport_{now_compact}_{uuid.uuid4().hex[:6]}.pdf"
    
    output_path = os.path.join(PDF_DIR, filename)

    # Extract doc title from first heading for the visual PDF title
    m = re.search(r"^#+\s+(.+)", markdown_text, re.MULTILINE)
    title = m.group(1).strip() if m else "Analysis Report"
    if len(title) > 80:
        title = title[:77] + "..."

    theme     = _theme(doc_type)
    body_html = _md_to_html_body(markdown_text)

    # Use colorful design if a template was uploaded, otherwise use academic black & white.
    # `_HTML_TEMPLATE` used to be referenced here, but the actual constant is
    # `_HTML_TEMPLATE_COLOR`; the old name made all template-based PDF renders fail.
    chosen_template = _HTML_TEMPLATE_COLOR if is_template else _HTML_TEMPLATE_BW

    html = chosen_template.format(
        title            = title,
        badge            = theme["badge"],
        accent           = theme["accent"],
        accent_lt        = theme["accent_lt"],
        now              = now_str,
        report_type      = theme["badge"].split(" ", 1)[1],
        body             = body_html,
    )

    # ── Render using isolated subprocess worker ───────────────────
    # Writing HTML to a temporary file
    tmp_html_name = f"tmp_{uuid.uuid4().hex[:8]}.html"
    tmp_html_path = os.path.join(PDF_DIR, tmp_html_name)
    with open(tmp_html_path, "w", encoding="utf-8") as f:
        f.write(html)
        
    try:
        # Run isolated process - guaranteed no event loop conflicts
        import sys
        worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_worker.py")
        import asyncio

        async def _run_worker_once():
            return await asyncio.to_thread(
                subprocess.run,
                [sys.executable, worker_script, tmp_html_path, output_path],
                check=False,
                capture_output=True,
                text=True,
            )

        # Retry up to 3 times for robustness against 0KB or layout crash bugs
        last_err = None
        for attempt in range(3):
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass

            cp = await _run_worker_once()
            
            # Check 1: Process crash
            if cp.returncode != 0:
                last_err = RuntimeError(
                    f"PDF rendering engine crashed (Exit Code {cp.returncode}).\n"
                    f"Possible cause: Layout complexity or missing dependencies.\n"
                    f"Details: {(cp.stderr or cp.stdout or '').strip()[-200:]}"
                )
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
                
            # Check 2: File missing
            if not os.path.exists(output_path):
                last_err = RuntimeError("PDF worker finished, but the output file is completely missing.")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
                
            # Check 3: 0KB File (or too small to be a valid PDF)
            out_size = os.path.getsize(output_path)
            if out_size == 0:
                last_err = RuntimeError(f"0KB File Corruption Error. The browser engine silently failed and wrote 0 bytes. Please retry generating the report.")
                await asyncio.sleep(1.0)
                continue
            elif out_size < 1024:
                last_err = RuntimeError(f"Invalid PDF Size ({out_size} bytes). The file is too small to be a valid document.")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
                
            # Success
            last_err = None
            break
            
        if last_err is not None:
            raise last_err

        with open(output_path, "rb") as f:
            fs.put(f.read(), filename=filename, content_type="application/pdf")
        os.remove(output_path)
    finally:
        # Cleanup temp HTML
        if os.path.exists(tmp_html_path):
            os.remove(tmp_html_path)

    return "", filename
