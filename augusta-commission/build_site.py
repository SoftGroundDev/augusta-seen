# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "markdown-it-py",
# ]
# ///
"""Build a local static website from the meeting transcripts and summaries.

Usage:
    .venv\\Scripts\\python augusta-commission\\build_site.py
    # then open augusta-commission\\site\\index.html in a browser
"""

import html
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).parent
SUMMARIES = ROOT / "summaries"
TRANSCRIPTS = ROOT / "transcripts"
OUT = ROOT / "site"

TIMESTAMP = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
TRANSCRIPT_LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] ?(.*)$")
md = MarkdownIt("commonmark", {"html": False}).enable("table")


@dataclass
class Meeting:
    stem: str
    title: str
    date: str
    video_url: str
    video_id: str
    duration: str
    lines: list[tuple[int, str]]
    summary_md: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def body(self) -> str:
        t = self.title.lower()
        for key, label in [
            ("election", "Board of Elections"),
            ("zoning appeals", "Board of Zoning Appeals"),
            ("planning", "Planning Commission"),
            ("historic", "Historic Preservation"),
            ("merit", "RCSO Merit Board"),
            ("budget", "Budget"),
            ("committee", "Committee Meetings"),
            ("commission meeting", "Commission Meetings"),
        ]:
            if key in t:
                return label
        return "Public Meetings and Hearings"


def seconds(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def hms(total: int) -> str:
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, block, body = text.split("---", 2)
    meta: dict = {}
    key = None
    for line in block.splitlines():
        if m := re.match(r"^\s+-\s+(.*)$", line):
            meta.setdefault(key, []).append(m.group(1).strip())
        elif m := re.match(r"^([\w.]+):\s*(.*)$", line):
            key, value = m.groups()
            meta[key] = value.strip() if value.strip() else []
    return meta, body.lstrip()


def parse_transcript(path: Path) -> Meeting:
    text = path.read_text(encoding="utf-8")
    title = re.search(r"^# (.+)$", text, re.M).group(1).strip()

    def field_(name: str) -> str:
        m = re.search(rf"^\* {name}: (.*)$", text, re.M)
        return m.group(1).strip() if m else ""

    video_url = field_("Video").strip("<>")
    vid = re.search(r"[?&]v=([\w-]+)", video_url)
    lines = [
        (seconds(*lm.groups()[:3]), lm.group(4))
        for line in text.split("## Transcript", 1)[-1].splitlines()
        if (lm := TRANSCRIPT_LINE.match(line.strip()))
    ]
    return Meeting(
        stem=path.stem,
        title=title,
        date=field_("Date"),
        video_url=video_url,
        video_id=vid.group(1) if vid else "",
        duration=field_("Duration"),
        lines=lines,
    )


def section(markdown_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", markdown_text, re.M | re.S)
    return m.group(1).strip() if m else ""


def plain(text: str) -> str:
    """Strip timestamp citations and inline markdown for short previews."""
    text = TIMESTAMP.sub("", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"\s+([.,;:)])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def tldr_bullets(meeting: Meeting) -> list[str]:
    if not meeting.summary_md:
        return []
    return [plain(b) for b in re.findall(r"^\* (.+)$", section(meeting.summary_md, "TL;DR"), re.M)]


def nearest_line(meeting: Meeting, t: int) -> int:
    """Start time of the transcript line covering second t."""
    best = meeting.lines[0][0] if meeting.lines else 0
    for start, _ in meeting.lines:
        if start > t:
            break
        best = start
    return best


def link_timestamps(markdown_text: str, meeting: Meeting) -> str:
    def repl(m: re.Match) -> str:
        t = seconds(*m.groups())
        anchor = hms(nearest_line(meeting, t)).replace(":", "")
        return f"[{m.group(1)}:{m.group(2)}:{m.group(3)}](../transcripts/{meeting.stem}.html#t-{anchor})"

    return TIMESTAMP.sub(repl, markdown_text)


def page(title: str, body: str, depth: int = 0, script: str = "") -> str:
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Augusta Seen</title>
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<header class="site-header">
  <a class="brand" href="{up}index.html">Augusta Seen</a>
  <nav><a href="{up}index.html">Meetings</a></nav>
</header>
<main>
{body}
</main>
<footer>Transcripts are machine-generated with Whisper and unreviewed. Summaries are AI-synthesized; verify against the video before relying on them.</footer>
{f"<script>{script}</script>" if script else ""}
</body>
</html>
"""


def render_index(meetings: list[Meeting]) -> str:
    bodies = sorted({m.body for m in meetings})
    options = "".join(f'<option value="{html.escape(b)}">{html.escape(b)}</option>' for b in bodies)
    cards = []
    for m in meetings:
        bullets = tldr_bullets(m)
        keywords = m.meta.get("keywords", []) if isinstance(m.meta.get("keywords"), list) else []
        tags = "".join(f'<span class="tag">{html.escape(k)}</span>' for k in keywords)
        preview = f"<p>{html.escape(bullets[0])}</p>" if bullets else '<p class="muted">No summary yet. Transcript only.</p>'
        primary = f"meetings/{m.stem}.html" if m.summary_md else f"transcripts/{m.stem}.html"
        search = " ".join([m.title, m.date, m.body, *keywords, *bullets]).lower()
        cards.append(f"""<article class="card" data-body="{html.escape(m.body)}" data-search="{html.escape(search)}">
  <div class="card-meta"><time>{m.date}</time> · {html.escape(m.body)} · {html.escape(m.duration)}</div>
  <h2><a href="{primary}">{html.escape(m.title)}</a></h2>
  {preview}
  <div class="tags">{tags}</div>
  <div class="card-links">{f'<a href="meetings/{m.stem}.html">Summary</a>' if m.summary_md else ''}<a href="transcripts/{m.stem}.html">Transcript</a><a href="{html.escape(m.video_url)}" target="_blank" rel="noopener">Video ↗</a></div>
</article>""")
    body = f"""<section class="intro">
  <h1>Augusta, GA public meetings</h1>
  <p>{len(meetings)} meetings from the <a href="https://www.youtube.com/user/augustagagov/streams" target="_blank" rel="noopener">Augusta GA government stream</a>, with transcripts and summaries of decisions, public comment, and follow-ups.</p>
</section>
<div class="filters">
  <input id="q" type="search" placeholder="Search titles, topics, and summaries…" autofocus>
  <select id="body"><option value="">All bodies</option>{options}</select>
  <span id="count" class="muted"></span>
</div>
<div id="cards">
{chr(10).join(cards)}
</div>"""
    script = """
const q = document.getElementById('q'), sel = document.getElementById('body'), count = document.getElementById('count');
const cards = [...document.querySelectorAll('.card')];
function filter() {
  const terms = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
  let shown = 0;
  for (const c of cards) {
    const ok = (!sel.value || c.dataset.body === sel.value) && terms.every(t => c.dataset.search.includes(t));
    c.hidden = !ok; if (ok) shown++;
  }
  count.textContent = `${shown} of ${cards.length}`;
}
q.addEventListener('input', filter); sel.addEventListener('change', filter); filter();
"""
    return page("Meetings", body, 0, script)


def render_meeting(m: Meeting) -> str:
    # Soft Ground Signals are internal business notes; keep them out of the public site.
    summary = re.sub(r"^## Soft Ground Signals\s*$.*?(?=^## |\Z)", "", m.summary_md, flags=re.M | re.S)
    content = md.render(link_timestamps(summary, m))
    content = re.sub(r'href="\.\./transcripts/([^"]+)\.md"', r'href="../transcripts/\1.html"', content)
    content = content.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    title = m.meta.get("title") or m.title
    header = f"""<p class="crumbs"><a href="../index.html">Meetings</a> / {html.escape(m.date)}</p>
<h1>{html.escape(title)}</h1>
<p class="card-meta">{html.escape(m.body)} · {html.escape(m.duration)} ·
  <a href="../transcripts/{m.stem}.html">Transcript</a> ·
  <a href="{html.escape(m.video_url)}" target="_blank" rel="noopener">Video ↗</a></p>
<p class="muted">Timestamps link to the matching line in the transcript, which links on to the video.</p>"""
    return page(title, f'{header}<article class="summary">{content}</article>', 1)


def render_transcript(m: Meeting) -> str:
    rows = []
    for start, text in m.lines:
        ts = hms(start)
        anchor = ts.replace(":", "")
        rows.append(
            f'<li id="t-{anchor}"><a class="ts" href="{html.escape(m.video_url)}&amp;t={start}s" '
            f'target="_blank" rel="noopener" title="Play from {ts}">{ts}</a> '
            f"<span>{html.escape(text)}</span></li>"
        )
    summary_link = f'<a href="../meetings/{m.stem}.html">Summary</a> · ' if m.summary_md else ""
    body = f"""<p class="crumbs"><a href="../index.html">Meetings</a> / {html.escape(m.date)}</p>
<h1>{html.escape(m.title)}: transcript</h1>
<p class="card-meta">{html.escape(m.date)} · {html.escape(m.duration)} · {summary_link}<a href="{html.escape(m.video_url)}" target="_blank" rel="noopener">Video ↗</a></p>
<p class="muted">Machine-generated with Whisper large-v3-turbo; unreviewed. Names and numbers are often mis-transcribed. Click a timestamp to play the video from that point.</p>
<div class="filters"><input id="q" type="search" placeholder="Filter lines…"><span id="count" class="muted"></span></div>
<ol class="transcript">
{chr(10).join(rows)}
</ol>"""
    script = """
const q = document.getElementById('q'), count = document.getElementById('count');
const rows = [...document.querySelectorAll('.transcript li')];
q.addEventListener('input', () => {
  const t = q.value.toLowerCase(); let shown = 0;
  for (const r of rows) { const ok = !t || r.textContent.toLowerCase().includes(t); r.hidden = !ok; if (ok) shown++; }
  count.textContent = t ? `${shown} matching lines` : '';
});
"""
    return page(f"{m.title} {m.date} transcript", body, 1, script)



CSS = """
:root { --fg:#1d2330; --muted:#5d6778; --bg:#f6f7f9; --card:#fff; --line:#dde1e8; --accent:#0b6e4f; --accent-soft:#e3f2ec; }
* { box-sizing:border-box; }
body { margin:0; font:16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color:var(--fg); background:var(--bg); }
a { color:var(--accent); }
.site-header { display:flex; justify-content:space-between; align-items:center; padding:.8rem 1.5rem; background:var(--fg); }
.site-header a { color:#fff; text-decoration:none; margin-left:1.2rem; }
.site-header .brand { font-weight:700; margin-left:0; font-size:1.1rem; }
main { max-width:1100px; margin:0 auto; padding:1.5rem; }
footer { max-width:1100px; margin:0 auto; padding:1.5rem; color:var(--muted); font-size:.85rem; border-top:1px solid var(--line); }
h1 { line-height:1.2; }
.muted { color:var(--muted); }
.intro p { color:var(--muted); max-width:70ch; }
.filters { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin:1rem 0 1.25rem; position:sticky; top:0; background:var(--bg); padding:.5rem 0; z-index:1; }
.filters input, .filters select { font:inherit; padding:.5rem .7rem; border:1px solid var(--line); border-radius:6px; background:#fff; }
.filters input { flex:1; min-width:240px; }
#cards { display:grid; gap:1rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:1rem 1.25rem; }
.card h2 { margin:.2rem 0 .4rem; font-size:1.2rem; }
.card h2 a { color:var(--fg); text-decoration:none; }
.card h2 a:hover { color:var(--accent); }
.card p { margin:.3rem 0 .6rem; }
.card-meta { color:var(--muted); font-size:.9rem; }
.tags { display:flex; flex-wrap:wrap; gap:.35rem; }
.tag { background:var(--accent-soft); color:var(--accent); border-radius:999px; padding:.1rem .6rem; font-size:.8rem; }
.card-links { margin-top:.7rem; display:flex; gap:1rem; font-size:.9rem; }
.crumbs { color:var(--muted); font-size:.9rem; margin:0; }
.summary { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:.5rem 1.75rem 1.5rem; }
.summary h2 { border-bottom:1px solid var(--line); padding-bottom:.3rem; margin-top:2rem; }
.summary a[href*="#t-"] { font-family:ui-monospace, Consolas, monospace; font-size:.8em; text-decoration:none; background:var(--accent-soft); padding:0 .25rem; border-radius:4px; white-space:nowrap; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.9rem; background:var(--card); }
th, td { border:1px solid var(--line); padding:.45rem .6rem; text-align:left; vertical-align:top; }
th { background:#eef1f5; }
.transcript { list-style:none; padding:0; background:var(--card); border:1px solid var(--line); border-radius:10px; }
.transcript li { display:flex; gap:.9rem; padding:.2rem 1rem; }
.transcript li:target { background:#fff3c4; }
.transcript .ts { font-family:ui-monospace, Consolas, monospace; font-size:.85rem; text-decoration:none; flex:none; padding-top:.1rem; }
:target { scroll-margin-top:4rem; }
"""


def main() -> None:
    meetings = {p.stem: parse_transcript(p) for p in TRANSCRIPTS.glob("*.md")}
    for path in SUMMARIES.glob("*.md"):
        if path.stem in meetings:
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
            meetings[path.stem].summary_md, meetings[path.stem].meta = body, meta
        else:
            print(f"warning: summary without transcript: {path.name}")
    ordered = sorted(meetings.values(), key=lambda m: (m.date, m.stem), reverse=True)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "meetings").mkdir(parents=True)
    (OUT / "transcripts").mkdir()
    (OUT / "style.css").write_text(CSS.lstrip(), encoding="utf-8")
    (OUT / "index.html").write_text(render_index(ordered), encoding="utf-8")
    for m in ordered:
        (OUT / "transcripts" / f"{m.stem}.html").write_text(render_transcript(m), encoding="utf-8")
        if m.summary_md:
            (OUT / "meetings" / f"{m.stem}.html").write_text(render_meeting(m), encoding="utf-8")
    summarized = sum(1 for m in ordered if m.summary_md)
    print(f"built {len(ordered)} meetings ({summarized} with summaries) -> {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
