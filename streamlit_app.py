#!/usr/bin/env python3
"""
MathType / WIRIS HTML → Clean HTML with LaTeX
Single-file Streamlit app that runs both converters side-by-side.

Renderers
  V1 – mml2tex   (Saxon/XSLT2, pip install mml2tex)
  V2 – mathml2tex (XSLT1/lxml,  pip install git+https://github.com/stultus/mathml2tex)

Usage:
  streamlit run app.py
"""

# ──────────────────────────────────────────────────────────────────────────────
# stdlib
# ──────────────────────────────────────────────────────────────────────────────
import re
import sys
import html as html_mod
import subprocess
import os
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Optional MathML libraries
# ──────────────────────────────────────────────────────────────────────────────
try:
    from mml2tex import mml_to_latex as _mml2tex_fn
    MML2TEX_OK = True
except ImportError:
    MML2TEX_OK = False

try:
    from mathml2tex import convert_mathml2tex as _mathml2tex_fn
    MATHML2TEX_OK = True
except ImportError:
    MATHML2TEX_OK = False

JS_WRAPPER = Path(__file__).parent / "convert_mathml.js"


# ══════════════════════════════════════════════════════════════════════════════
# MathML → LaTeX helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_xmlns(s):
    if 'xmlns' not in s:
        s = s.replace('<math', '<math xmlns="http://www.w3.org/1998/Math/MathML"', 1)
    return s


def _normalise_latex(latex, strip_display_env=False):
    """Single-line, pmatrix substitution, optional \\[ \\] stripping."""
    latex = re.sub(r'\s+', ' ', latex).strip()
    if strip_display_env:
        latex = re.sub(r'^\\\[\s*', '', latex)
        latex = re.sub(r'\s*\\\]$', '', latex)
        latex = re.sub(r'\\phantom\{\\rule\{[^}]*\}\{[^}]*\}\}', ' ', latex)
        latex = re.sub(r'\s+', ' ', latex).strip()
    # \left(\begin{array}{c…}\end{array}\right) → \begin{pmatrix}…\end{pmatrix}
    latex = re.sub(
        r'\\left\(\\begin\{array\}\{c+\}(.*?)\\end\{array\}\\right\)',
        r'\\begin{pmatrix}\1\\end{pmatrix}', latex, flags=re.DOTALL)
    latex = re.sub(
        r'\(\\begin\{array\}\{c+\}(.*?)\\end\{array\}\)',
        r'\\begin{pmatrix}\1\\end{pmatrix}', latex, flags=re.DOTALL)
    return latex


def _node_convert(mathml_str):
    """Node.js bridge (local dev fallback)."""
    try:
        proc = subprocess.run(
            ["node", str(JS_WRAPPER)],
            input=mathml_str, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            return _normalise_latex(proc.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Python hand-written fallback (needed for bordered tables)
# ──────────────────────────────────────────────────────────────────────────────

TEX_GREEK = {
    "\u03b1": "\\alpha", "\u03b2": "\\beta", "\u03b3": "\\gamma",
    "\u03b4": "\\delta", "\u03b5": "\\epsilon", "\u03b6": "\\zeta",
    "\u03b7": "\\eta", "\u03b8": "\\theta", "\u03b9": "\\iota",
    "\u03ba": "\\kappa", "\u03bb": "\\lambda", "\u03bc": "\\mu",
    "\u03bd": "\\nu", "\u03be": "\\xi", "\u03c0": "\\pi",
    "\u03c1": "\\rho", "\u03c3": "\\sigma", "\u03c4": "\\tau",
    "\u03c6": "\\phi", "\u03c7": "\\chi", "\u03c8": "\\psi",
    "\u03c9": "\\omega", "\u0393": "\\Gamma", "\u0394": "\\Delta",
    "\u0398": "\\Theta", "\u039b": "\\Lambda", "\u03a0": "\\Pi",
    "\u03a3": "\\Sigma", "\u03a6": "\\Phi", "\u03a9": "\\Omega",
}

TAG_HANDLERS = {}


def _decode_text(text):
    text = html_mod.unescape(text)
    text = text.replace('\u00a0', ' ').replace('\u200b', '')
    for c, t in TEX_GREEK.items():
        text = text.replace(c, t)
    return text


def _math_ident(t):
    return t


def _split_children(xml):
    result, pos = [], 0
    while pos < len(xml):
        tag_start = xml.find('<', pos)
        if tag_start == -1:
            t = xml[pos:].strip()
            if t: result.append(t)
            break
        if tag_start > pos:
            t = xml[pos:tag_start].strip()
            if t: result.append(t)
        tag_end = xml.find('>', tag_start)
        if tag_end == -1: break
        if xml[tag_start + 1] == '/':
            pos = tag_end + 1; continue
        if xml[tag_end - 1] == '/':
            result.append(xml[tag_start:tag_end + 1])
            pos = tag_end + 1; continue
        m = re.match(r'<(\w+)', xml[tag_start:])
        if not m:
            pos = tag_end + 1; continue
        tag_name = m.group(1)
        close_tag = '</' + tag_name + '>'
        depth, search_start, close_pos = 1, tag_end + 1, -1
        while depth > 0:
            nxt_o = xml.find('<' + tag_name, search_start)
            nxt_c = xml.find(close_tag, search_start)
            if nxt_c == -1: break
            if nxt_o != -1 and nxt_o < nxt_c:
                depth += 1
                search_start = xml.find('>', nxt_o) + 1
            else:
                depth -= 1
                if depth == 0: close_pos = nxt_c
                else: search_start = nxt_c + len(close_tag)
        if close_pos != -1:
            result.append(xml[tag_start:close_pos + len(close_tag)])
            pos = close_pos + len(close_tag)
        else:
            pos = tag_end + 1
    return result


def _convert_children(xml):
    parts = _split_children(xml)
    result = []
    for part in parts:
        if part.startswith('<'):
            tm = re.match(r'^<(\w+)([^>]*)>(.*)</\1>$', part, re.DOTALL)
            if tm:
                tag, attrs_str, inner = tm.group(1), tm.group(2), tm.group(3)
                attrs = {}
                for m in re.finditer(r'(\w+)(?:\s*=\s*"([^"]*)")?', attrs_str):
                    attrs[m.group(1)] = m.group(2) if m.group(2) else m.group(1)
                h = TAG_HANDLERS.get(tag)
                result.append(h(inner, attrs) if h else _convert_children(inner))
            else:
                result.append(_decode_text(part))
        else:
            result.append(_decode_text(part))
    return ''.join(result)


def _convert_node(xml):
    xml = xml.strip()
    if not xml: return ""
    tm = re.match(r'^<(\w+)([^>]*)>(.*)</\1>\s*$', xml, re.DOTALL)
    if not tm:
        sc = re.match(r'^<(\w+)([^>]*)/>\s*$', xml, re.DOTALL)
        if sc: return ' ' if sc.group(1) == 'mspace' else ''
        return _decode_text(xml)
    tag, attrs_str, inner = tm.group(1), tm.group(2), tm.group(3)
    attrs = {}
    for m in re.finditer(r'(\w+)(?:\s*=\s*"([^"]*)")?', attrs_str):
        attrs[m.group(1)] = m.group(2) if m.group(2) else m.group(1)
    h = TAG_HANDLERS.get(tag)
    return h(inner, attrs) if h else _convert_children(inner)


def _split_mtr(t): return re.findall(r'<mtr[^>]*>(.*?)</mtr>', t, re.DOTALL)
def _split_mtd(t): return re.findall(r'<mtd[^>]*>(.*?)</mtd>', t, re.DOTALL)


def _handle_mfrac(inner):
    p = _split_children(inner)
    if len(p) < 2: return _convert_children(inner)
    return '\\frac{' + _convert_node(p[0]) + '}{' + _convert_node(p[1]) + '}'


def _handle_msup(inner):
    p = _split_children(inner)
    if len(p) < 2: return _convert_children(inner)
    return _convert_node(p[0]) + '^{' + _convert_node(p[1]) + '}'


def _handle_msub(inner):
    p = _split_children(inner)
    if len(p) < 2: return _convert_children(inner)
    return _convert_node(p[0]) + '_{' + _convert_node(p[1]) + '}'


def _handle_msubsup(inner):
    p = _split_children(inner)
    if len(p) < 3: return _convert_children(inner)
    return _convert_node(p[0]) + '_{' + _convert_node(p[1]) + '}^{' + _convert_node(p[2]) + '}'


def _handle_mover(inner, attrs):
    p = _split_children(inner)
    if len(p) < 2: return _convert_children(inner)
    base, over = _convert_node(p[0]), _convert_node(p[1])
    if attrs.get('accent') == 'true' and over in ('\u2192', '\\rightarrow', '→'):
        return '\\vec{' + base + '}'
    return '\\overset{' + over + '}{' + base + '}'


def _handle_munder(inner, attrs):
    p = _split_children(inner)
    if len(p) < 2: return _convert_children(inner)
    return '\\underset{' + _convert_node(p[1]) + '}{' + _convert_node(p[0]) + '}'


def _handle_mtable(inner, attrs):
    rows = _split_mtr(inner)
    if not rows: return '\\begin{matrix}\\end{matrix}'
    latex_rows = [' & '.join(_convert_children(c) for c in _split_mtd(r)) for r in rows]
    return '\\begin{matrix}' + ' \\\\ '.join(latex_rows) + '\\end{matrix}'


def _handle_menclose(inner, attrs):
    rows = _split_mtr(inner)
    if not rows: return _convert_children(inner)
    col_count = len(re.findall(r'<mtd[^>]*>', inner)) // max(len(rows), 1)
    latex_rows = [' & '.join(_convert_children(c) for c in _split_mtd(r)) for r in rows]
    body = ' \\\\\n\\hline\n'.join(latex_rows)
    return ('\\begin{array}{|' + '|'.join(['c'] * max(col_count, 1)) + '|}\n'
            '\\hline\n' + body + ' \\\\\n\\hline\n\\end{array}')


def _handle_mfenced(inner, attrs):
    o = attrs.get('open', '(')
    c = attrs.get('close', ')')
    joined = _convert_children(inner)
    omap = {'(': '(', '[': '[', '{': '\\{', '|': '|'}
    cmap = {')': ')', ']': ']', '}': '\\}', '|': '|'}
    return '\\left' + omap.get(o, o) + joined + '\\right' + cmap.get(c, c)


TAG_HANDLERS['mtext']    = lambda i, a: '\\text{' + _decode_text(i.strip()) + '}'
TAG_HANDLERS['mi']       = lambda i, a: _math_ident(_decode_text(i.strip()))
TAG_HANDLERS['mn']       = lambda i, a: _decode_text(i.strip())
TAG_HANDLERS['mo']       = lambda i, a: _convert_children(i)
TAG_HANDLERS['mfrac']    = lambda i, a: _handle_mfrac(i)
TAG_HANDLERS['msup']     = lambda i, a: _handle_msup(i)
TAG_HANDLERS['msub']     = lambda i, a: _handle_msub(i)
TAG_HANDLERS['msubsup']  = lambda i, a: _handle_msubsup(i)
TAG_HANDLERS['msqrt']    = lambda i, a: '\\sqrt{' + _convert_children(i) + '}'
TAG_HANDLERS['mover']    = _handle_mover
TAG_HANDLERS['munder']   = _handle_munder
TAG_HANDLERS['mrow']     = lambda i, a: _convert_children(i)
TAG_HANDLERS['mstyle']   = lambda i, a: _convert_children(i)
TAG_HANDLERS['mspace']   = lambda i, a: ' '
TAG_HANDLERS['mpadded']  = lambda i, a: _convert_children(i)
TAG_HANDLERS['mphantom'] = lambda i, a: _convert_children(i)
TAG_HANDLERS['mtable']   = _handle_mtable
TAG_HANDLERS['mtr']      = lambda i, a: i
TAG_HANDLERS['mtd']      = lambda i, a: _convert_children(i)
TAG_HANDLERS['menclose'] = _handle_menclose
TAG_HANDLERS['mfenced']  = _handle_mfenced


def _mathml_fallback(mathml_str):
    t = mathml_str.strip()
    if not t: return ""
    t = re.sub(r'<\?xml[^>]*\?>', '', t)
    t = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', '', t)
    t = re.sub(r'<math[^>]*>', '', t, count=1)
    t = re.sub(r'</math>', '', t, count=1)
    result = _convert_children(t.strip())
    return re.sub(r'\s+', ' ', result).strip()


def _has_bordered_table(s):
    return bool(re.search(r'<mtable[\s>][^>]*\b(rowlines|columnlines)\s*=', s, re.DOTALL))


def _preprocess_mathml(s):
    return s.replace('&nbsp;', ' ').replace('\u00a0', ' ')


# ──────────────────────────────────────────────────────────────────────────────
# Public converters: v1 (mml2tex) and v2 (mathml2tex)
# ──────────────────────────────────────────────────────────────────────────────

def mathml_to_latex_v1(mathml_str):
    """mml2tex (Saxon) → Node.js → Python fallback."""
    mathml_str = _preprocess_mathml(mathml_str)
    if _has_bordered_table(mathml_str):
        return _mathml_fallback(mathml_str)
    if MML2TEX_OK:
        try:
            r = _mml2tex_fn(_ensure_xmlns(mathml_str))
            if r and r.strip():
                return _normalise_latex(r, strip_display_env=False)
        except Exception:
            pass
    r = _node_convert(mathml_str)
    if r: return r
    return _mathml_fallback(mathml_str)


def mathml_to_latex_v2(mathml_str):
    """mathml2tex (lxml/XSLT1) → Node.js → Python fallback."""
    mathml_str = _preprocess_mathml(mathml_str)
    if _has_bordered_table(mathml_str):
        return _mathml_fallback(mathml_str)
    if MATHML2TEX_OK:
        try:
            r = _mathml2tex_fn(_ensure_xmlns(mathml_str))
            if r and r.strip():
                return _normalise_latex(r, strip_display_env=True)
        except Exception:
            pass
    r = _node_convert(mathml_str)
    if r: return r
    return _mathml_fallback(mathml_str)


# ══════════════════════════════════════════════════════════════════════════════
# HTML parsing & conversion pipeline
# ══════════════════════════════════════════════════════════════════════════════

def _extract_tag(html, tag_name, start_pos=0):
    pat = re.compile(r'<' + re.escape(tag_name) + r'([\s>])')
    close_tag = '</' + tag_name + '>'
    sc_pat = re.compile(r'<' + re.escape(tag_name) + r'[\s>][^>]*/>')
    m = pat.search(html, start_pos)
    if not m: return None, -1
    tag_start = m.start()
    teo = html.find('>', m.end() - 1)
    if teo == -1: return None, -1
    tag_end_open = teo + 1
    if html[tag_start:tag_end_open].rstrip().endswith('/>'):
        return html[tag_start:tag_end_open], tag_end_open
    depth, pos = 1, tag_end_open
    while depth > 0 and pos < len(html):
        sc = sc_pat.search(html, pos)
        no = pat.search(html, pos)
        nc = html.find(close_tag, pos)
        if nc == -1: return None, -1
        if no and no.start() < nc:
            if sc and sc.start() == no.start():
                pos = sc.end(); continue
            depth += 1
            gt = html.find('>', no.end() - 1)
            pos = gt + 1 if gt != -1 else no.end()
        else:
            depth -= 1
            if depth == 0:
                return html[tag_start:nc + len(close_tag)], nc + len(close_tag)
            pos = nc + len(close_tag)
    return None, -1


def _extract_all_tags(html, tag_name, start_pos=0):
    results, pos = [], start_pos
    while pos < len(html):
        r, end = _extract_tag(html, tag_name, pos)
        if r is None: break
        results.append(r); pos = end
    return results


def _inject_svg_dimensions(svg):
    if 'width=' in svg[:100]: return svg
    rm = re.search(r'<rect[^>]*class="real"[^>]*width="([^"]+)"[^>]*height="([^"]+)"', svg)
    if not rm:
        rm = re.search(r'<rect[^>]*width="([^"]+)"[^>]*height="([^"]+)"[^>]*class="real"', svg)
    if rm:
        w, h = float(rm.group(1)) + 2, float(rm.group(2)) + 2
        # SVG opening tag may be followed by a space OR a newline
        svg = svg.replace('<svg ', f'<svg width="{w:.0f}" height="{h:.0f}" ', 1)
        svg = svg.replace('<svg\n', f'<svg width="{w:.0f}" height="{h:.0f}"\n', 1)
    return svg


def _extract_diagram_labels(de_html, mathml_to_latex_fn):
    labels = []
    for pc in re.finditer(r'<position-container([^>]*)>(.*?)</position-container>', de_html, re.DOTALL):
        attrs, content = pc.group(1), pc.group(2)
        lm = re.search(r'left\s*:\s*([\d.]+)px', attrs)
        tm = re.search(r'top\s*:\s*([\d.]+)px', attrs)
        if not lm or not tm: continue
        left, top = lm.group(1), tm.group(1)
        cm = (re.search(r'<compositeblock[^>]*style="[^"]*color:\s*([^;"]+)', content) or
              re.search(r'<block[^>]*style="[^"]*color:\s*([^;"]+)', content))
        color = cm.group(1).strip() if cm else 'inherit'
        parts = []
        for seg in re.finditer(
            r'<compositeblock[^>]*class="([^"]*)"[^>]*>(.*?)</compositeblock>'
            r'|<block([\s>][^>]*)>(.*?)</block>',
            content, re.DOTALL
        ):
            cb_class, cb_inner, blk_inner = seg.group(1), seg.group(2), seg.group(4)
            if cb_class is not None:
                if 'role-mathmode-area' in cb_class and 'inline' in cb_class:
                    dm = re.search(r'data-mathml="([^"]+)"', seg.group(0))
                    if dm:
                        latex = mathml_to_latex_fn(html_mod.unescape(dm.group(1)))
                        parts.append(f'${latex}$')
                    else:
                        t = re.sub(r'<[^>]+>', '', cb_inner)
                        t = html_mod.unescape(t).replace('\u00a0', ' ').strip()
                        if t: parts.append(html_mod.escape(t))
                elif 'text-sub-script-symbol' in cb_class:
                    t = re.sub(r'<[^>]+>', '', cb_inner)
                    t = html_mod.unescape(t).replace('\u00a0', ' ').strip()
                    if t: parts.append(f'<sub>{html_mod.escape(t)}</sub>')
            elif blk_inner is not None:
                t = re.sub(r'<[^>]+>', '', blk_inner)
                t = html_mod.unescape(t).replace('\u00a0', ' ').strip()
                if t: parts.append(html_mod.escape(t))
        if not parts: continue
        labels.append(
            f'<span style="position:absolute;left:{left}px;top:{top}px;'
            f'color:{color};font-size:13px;font-family:serif;white-space:nowrap;">'
            + ''.join(parts) + '</span>')
    return '\n'.join(labels)


def _extract_svg_from_diagram(elem_str, mathml_to_latex_fn):
    md = re.search(r'<math-diagram[^>]*>(.*?)</math-diagram>', elem_str, re.DOTALL)
    de_m = re.search(r'<diagram-editors>(.*?)</diagram-editors>', elem_str, re.DOTALL)
    labels = _extract_diagram_labels(de_m.group(1), mathml_to_latex_fn) if de_m else ''
    if md:
        inner = md.group(1)
        svgs = re.findall(r'(<svg[^>]*class="role-diagram-draw-area"[^>]*>.*?</svg>)', inner, re.DOTALL)
        if not svgs:
            svgs = [s for s in re.findall(r'(<svg[^>]*>.*?</svg>)', inner, re.DOTALL) if len(s) > 100]
        if svgs:
            svg = re.sub(r'\s+', ' ', _inject_svg_dimensions(svgs[0]))
            # Read dimensions from <rect class="real"> — NOT from the first
            # width= in the SVG (which hits stroke-width="0.5" on <line> elements)
            rm = (re.search(r'<rect[^>]*class="real"[^>]*width="([^"]+)"[^>]*height="([^"]+)"', svg) or
                  re.search(r'<rect[^>]*width="([^"]+)"[^>]*height="([^"]+)"[^>]*class="real"', svg))
            w = str(float(rm.group(1)) + 2) if rm else '800'
            h = str(float(rm.group(2)) + 2) if rm else '302'
            if labels:
                return (f'<div style="position:relative;display:inline-block;'
                        f'width:{w}px;height:{h}px;">{svg}{labels}</div>')
            return svg
    sm = re.search(r'(<svg[^>]*>.*?</svg>)', elem_str, re.DOTALL)
    if sm:
        return re.sub(r'\s+', ' ', _inject_svg_dimensions(sm.group(1)))
    return None


def _extract_math_container(html, role_class, start_pos=0):
    pattern = r'<compositeblock[^>]*' + re.escape(role_class) + r'[^>]*>'
    m = re.search(pattern, html[start_pos:])
    if not m: return None, -1
    abs_start = start_pos + m.start()
    result, end = _extract_tag(html, 'compositeblock', abs_start)
    if result: return result, end
    ote = html.find('>', abs_start)
    if ote == -1: return None, -1
    ot = html[abs_start:ote + 1]
    if 'data-mathml' in ot: return ot, len(html)
    return None, -1


def _extract_math_from_container(container_html):
    m = re.search(r'<span[^>]*role="presentation"[^>]*>(<math[ >].*?</math>)', container_html, re.DOTALL)
    if m: return m.group(1)
    m = re.search(r'<math[ >].*?</math>', container_html, re.DOTALL)
    if m: return m.group(0)
    m = re.search(r'data-mathml="([^"]*)"', container_html)
    if m: return html_mod.unescape(m.group(1))
    return None


def _extract_diagram_el(html, start_pos=0):
    pattern = r'<compositeblock[^>]*class="[^"]*math-diagram[^"]*"[^>]*>'
    m = re.search(pattern, html[start_pos:])
    if m:
        abs_start = start_pos + m.start()
        result, end = _extract_tag(html, 'compositeblock', abs_start)
        if result: return result, end
        return html[abs_start:], len(html)
    return None, -1


def _is_empty_line(line_html):
    blocks = re.findall(r'<block[^>]*>(.*?)</block>', line_html, re.DOTALL)
    if not blocks: return True
    text = re.sub(r'<[^>]+>', '', ''.join(blocks))
    return html_mod.unescape(text).strip() in ('', '\u00a0', '&nbsp;')


def _extract_heading_text(line_html):
    blocks = re.findall(r'<block[^>]*>(.*?)</block>', line_html, re.DOTALL)
    text = re.sub(r'<[^>]+>', '', ''.join(blocks))
    return html_mod.unescape(text).replace('\u00a0', ' ').replace('\u200b', '').strip()


def _extract_iframe(line_html):
    m = re.search(r'&lt;iframe\s+([^&]*)&gt;&lt;/iframe&gt;', line_html)
    if m: return f'<iframe {html_mod.unescape(m.group(1))}></iframe>'
    m2 = re.search(r'<block[^>]*>(.*?)</block>', line_html, re.DOTALL)
    if m2:
        content = m2.group(1)
        im = re.search(r'<iframe\s+([^>]*)></iframe>', content, re.IGNORECASE)
        if im: return f'<iframe {im.group(1)}></iframe>'
        raw = html_mod.unescape(content).replace('&lt;', '<').replace('&gt;', '>')
        return raw
    return line_html


def _extract_blocks(line_html, mathml_to_latex_fn):
    parts, pos = [], 0
    block_pat = re.compile(r'<block([\s>])')
    while pos < len(line_html):
        block_tag, b_end = _extract_tag(line_html, 'block', pos)
        inline_math_tag, im_end = _extract_math_container(line_html, 'role-mathmode-area inline', pos)
        diagram_tag, d_end = _extract_diagram_el(line_html, pos)
        candidates = []
        if block_tag and b_end > 0:
            bm = block_pat.search(line_html, pos)
            if bm: candidates.append((bm.start(), 'block', block_tag, b_end))
        if inline_math_tag and im_end > 0:
            im_m = re.search(r'<compositeblock[^>]*role-mathmode-area inline', line_html[pos:])
            im_start = pos + im_m.start() if im_m else pos
            candidates.append((im_start, 'inline_math', inline_math_tag, im_end))
        if diagram_tag and d_end > 0:
            dm_m = re.search(r'<compositeblock[^>]*math-diagram', line_html[pos:])
            dm_start = pos + dm_m.start() if dm_m else pos
            candidates.append((dm_start, 'diagram', diagram_tag, d_end))
        candidates = [c for c in candidates if c[0] >= pos]
        if not candidates: break
        candidates.sort()
        ft, fc, fe, fs = candidates[0][1], candidates[0][2], candidates[0][3], candidates[0][0]
        if fs > pos:
            tb = re.sub(r'<[^>]+>', '', html_mod.unescape(line_html[pos:fs]))
            tb = tb.replace('\u00a0', ' ').replace('\u200b', '').strip()
            if tb: parts.append(html_mod.escape(tb))
        if ft == 'block':
            bam = re.search(r'<block\b([^>]*)>', fc)
            ba = bam.group(1) if bam else ''
            bi_start = fc.find('>') + 1
            bi_end = fc.rfind('</block>')
            bi = html_mod.unescape(fc[bi_start:bi_end]).replace('\u00a0', ' ').replace('\u200b', '')
            style = re.search(r'style="([^"]*)"', ba)
            bold = 'bold' in ba or (style and 'bold' in style.group(1))
            italic = 'italic' in ba or (style and 'italic' in style.group(1))
            if bold and italic: parts.append(f'<b><i>{html_mod.escape(bi)}</i></b>')
            elif bold: parts.append(f'<b>{html_mod.escape(bi)}</b>')
            elif italic: parts.append(f'<i>{html_mod.escape(bi)}</i>')
            else: parts.append(html_mod.escape(bi))
            pos = fe
        elif ft == 'inline_math':
            mt = _extract_math_from_container(fc)
            if mt:
                latex = mathml_to_latex_fn(mt)
                parts.append(f'${latex}$')
            pos = fe
        elif ft == 'diagram':
            svg = _extract_svg_from_diagram(fc, mathml_to_latex_fn)
            if svg: parts.append(f'<div class="diagram">{svg}</div>')
            pos = fe
    remaining = re.sub(r'<[^>]+>', '', html_mod.unescape(line_html[pos:]))
    remaining = remaining.replace('\u00a0', ' ').replace('\u200b', '').strip()
    if remaining: parts.append(html_mod.escape(remaining))
    return parts


def _convert_display_math(line_html, mathml_to_latex_fn):
    mt = _extract_math_from_container(line_html)
    if mt:
        latex = mathml_to_latex_fn(mt)
        if latex: return f'$${latex}$$'
    for m in re.finditer(r'data-mathml="([^"]*)"', line_html):
        latex = mathml_to_latex_fn(html_mod.unescape(m.group(1)))
        if latex: return f'$${latex}$$'
    return None


def process_html(html_content, mathml_to_latex_fn, verbose=False):
    """Convert WIRIS/MathType HTML to clean HTML using the given MathML converter."""
    cs = html_content.find('<body')
    if cs == -1: return html_content
    body_start = html_content.find('>', cs) + 1
    body_end = html_content.find('</body>', body_start)
    if body_end == -1: body_end = len(html_content)
    body_content = html_content[body_start:body_end]
    hm = re.search(r'<head>(.*?)</head>', html_content, re.DOTALL | re.IGNORECASE)
    head_content = hm.group(1) if hm else ''
    tm = re.search(r'<title>(.*?)</title>', head_content, re.DOTALL | re.IGNORECASE)
    title = tm.group(1) if tm else 'Converted Document'
    editor_area, _ = _extract_tag(body_content, 'editarea', 0)
    if editor_area is None:
        content_area = body_content
    else:
        content_area = editor_area[editor_area.find('>') + 1:editor_area.rfind('</editarea>')]
    area_container, _ = _extract_tag(content_area, 'area-container', 0)
    if area_container:
        content_area = area_container[area_container.find('>') + 1:area_container.rfind('</area-container>')]
    lines = _extract_all_tags(content_area, 'line', 0)
    output_parts, current_paragraph = [], []
    for line_html in lines:
        line_html = line_html.strip()
        if not line_html: continue
        if _is_empty_line(line_html):
            if current_paragraph:
                output_parts.append('<p>' + ''.join(current_paragraph).strip() + '</p>')
                current_paragraph = []
            continue
        has_diagram = 'class="math-diagram"' in line_html or '<math-diagram' in line_html
        has_display = 'role-mathmode-area display' in line_html
        has_iframe = '&lt;iframe' in line_html or ('iframe' in line_html.lower() and 'src=' in line_html.lower())
        is_heading = bool(re.search(r'font-size\s*:\s*1\.\d+em', line_html))
        if has_iframe or has_diagram or has_display or is_heading:
            if current_paragraph:
                output_parts.append('<p>' + ''.join(current_paragraph).strip() + '</p>')
                current_paragraph = []
            if has_iframe:
                output_parts.append(_extract_iframe(line_html))
            elif has_diagram:
                svg = _extract_svg_from_diagram(line_html, mathml_to_latex_fn)
                if svg: output_parts.append(f'<div class="diagram">{svg}</div>')
            elif has_display:
                latex = _convert_display_math(line_html, mathml_to_latex_fn)
                if latex: output_parts.append(latex)
            elif is_heading:
                output_parts.append(f'<h2>{html_mod.escape(_extract_heading_text(line_html))}</h2>')
            continue
        current_paragraph.extend(_extract_blocks(line_html, mathml_to_latex_fn))
    if current_paragraph:
        output_parts.append('<p>' + ''.join(current_paragraph).strip() + '</p>')
    body_html = '\n'.join(output_parts)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{html_mod.escape(title)}</title>
</head>
<body>
{body_html}
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Streamlit UI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import streamlit as st

    st.set_page_config(page_title="MathType HTML Converter", layout="wide")
    st.title("MathType / WIRIS HTML Converter")
    st.markdown(
        "Convert your MathType/WIRIS editor HTML to clean HTML with **MathJax-ready LaTeX**. "
        "Both renderers run in parallel — compare and download the one you prefer."
    )

    # ── library status badges ──────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("V1 mml2tex", "✓ available" if MML2TEX_OK else "✗ not installed",
              "pip install mml2tex")
    c2.metric("V2 mathml2tex", "✓ available" if MATHML2TEX_OK else "✗ not installed",
              "pip install git+https://github.com/stultus/mathml2tex")
    c3.metric("Python fallback", "✓ always available", "for bordered tables")

    st.divider()

    # ── input ─────────────────────────────────────────────────────────────
    uploaded = st.file_uploader("Upload HTML file", type=["html", "htm"])
    input_html = st.text_area(
        "Or paste raw MathType/WIRIS HTML here:",
        height=250,
        placeholder="Paste the full HTML from the MathType/WIRIS editor...",
    )
    if uploaded:
        input_html = uploaded.read().decode("utf-8")
        st.info(f"Loaded **{uploaded.name}** — {len(input_html):,} bytes")

    convert_btn = st.button("Convert with both renderers", type="primary", use_container_width=True)

    if convert_btn:
        if not input_html.strip():
            st.error("Please upload a file or paste HTML above.")
        else:
            with st.spinner("Running V1 (mml2tex)…"):
                try:
                    out_v1 = process_html(input_html, mathml_to_latex_v1)
                    st.session_state["out_v1"] = out_v1
                    st.session_state["err_v1"] = None
                except Exception as e:
                    st.session_state["out_v1"] = None
                    st.session_state["err_v1"] = str(e)

            with st.spinner("Running V2 (mathml2tex)…"):
                try:
                    out_v2 = process_html(input_html, mathml_to_latex_v2)
                    st.session_state["out_v2"] = out_v2
                    st.session_state["err_v2"] = None
                except Exception as e:
                    st.session_state["out_v2"] = None
                    st.session_state["err_v2"] = str(e)

    # ── output ────────────────────────────────────────────────────────────
    if "out_v1" in st.session_state or "out_v2" in st.session_state:
        st.divider()
        tab1, tab2 = st.tabs(["V1 — mml2tex (Saxon/XSLT2)", "V2 — mathml2tex (lxml/XSLT1)"])

        for tab, key_out, key_err, fname, label in [
            (tab1, "out_v1", "err_v1", "converted_v1.html", "V1"),
            (tab2, "out_v2", "err_v2", "converted_v2.html", "V2"),
        ]:
            with tab:
                err = st.session_state.get(key_err)
                out = st.session_state.get(key_out)
                if err:
                    st.error(f"Conversion failed: {err}")
                elif out:
                    st.success(f"{label} — {len(out):,} bytes")
                    st.download_button(
                        f"⬇ Download {fname}",
                        data=out,
                        file_name=fname,
                        mime="text/html",
                        use_container_width=True,
                        key=f"dl_{key_out}",
                    )
                    st.text_area(f"Raw HTML ({label}):", value=out, height=400, key=f"ta_{key_out}")


if __name__ == "__main__":
    main()
