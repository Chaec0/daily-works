"""저장소 주인이 쓴 열린 Issue 들을 읽어 정적 사이트(_site/)를 만든다. (GitHub Actions 에서 실행)

- Issue 하나 = 작업물 하나 (제목 = Issue 제목, 글·첨부 = Issue 본문)
- 날짜 = Issue 를 만든 날짜(한국 시간). 수정해도 바뀌지 않고, 수정 시각은 표시하지 않는다.
- 닫힌 Issue, 다른 사람이 쓴 Issue 는 사이트에 나오지 않는다.
- 본문은 글자 그대로 보여준다(프롬프트가 깨지지 않도록). 첨부 이미지·영상·파일·유튜브 주소만 미디어로 바꾼다.
"""
import html
import json
import os
import re
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_site"
KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")

ASSET = r"https://github\.com/user-attachments/assets/[0-9a-fA-F-]+"
VIDEO_LINE = re.compile(rf"^\s*({ASSET})\s*$")  # 영상을 첨부하면 본문에 주소만 한 줄로 들어간다
YOUTUBE_LINE = re.compile(
    r"^\s*https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?\S*?v=|shorts/)|youtu\.be/)([\w-]{11})\S*\s*$")
IMG_TAG = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"[^>]*>')
IMG_MD = re.compile(r"!\[([^\]]*)\]\((\S+?)\)")
FILE_MD = re.compile(r"\[([^\]]+)\]\((https://github\.com/user-attachments/files/\S+?)\)")
URL = re.compile(r"https?://[^\s<>\"')\]]+")
DOC_EXT = {"pdf", "doc", "docx", "hwp", "hwpx", "xls", "xlsx", "ppt", "pptx", "txt", "md",
           "rtf", "odt", "key", "pages", "numbers", "epub", "csv"}


# ---------- GitHub ----------

def fetch_issues():
    owner = REPO.split("/")[0]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "daily-works"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    issues, page = [], 1
    while True:
        url = (f"https://api.github.com/repos/{REPO}/issues"
               f"?state=open&creator={owner}&per_page=100&page={page}")
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
            batch = json.load(resp)
        issues += [i for i in batch if "pull_request" not in i]
        if len(batch) < 100:
            break
        page += 1
    return sorted(issues, key=lambda i: i["created_at"], reverse=True)


# ---------- 본문 해석 ----------

def parse_body(body):
    """본문을 [('text', 글) | ('image', url) | ('video', url) | ('youtube', id) | ('file', (이름, url))] 로 나눈다."""
    blocks, text = [], []

    def flush():
        joined = "\n".join(text).strip("\n")
        if joined.strip():
            blocks.append(("text", joined))
        text.clear()

    for line in (body or "").replace("\r\n", "\n").split("\n"):
        if m := VIDEO_LINE.match(line):
            flush(); blocks.append(("video", m[1])); continue
        if m := YOUTUBE_LINE.match(line):
            flush(); blocks.append(("youtube", m[1])); continue
        images = IMG_TAG.findall(line) + [u for _, u in IMG_MD.findall(line)]
        files = FILE_MD.findall(line)
        if images or files:
            rest = FILE_MD.sub("", IMG_MD.sub("", IMG_TAG.sub("", line)))
            if rest.strip():
                text.append(rest.rstrip())
            flush()
            blocks += [("image", u) for u in images] + [("file", f) for f in files]
            continue
        text.append(line)
    flush()
    return blocks


def kind(blocks):
    types = {t for t, _ in blocks}
    if types & {"video", "youtube"}:
        return "영상"
    if "image" in types:
        return "이미지"
    for t, v in blocks:
        if t == "file":
            return "문서" if v[0].rsplit(".", 1)[-1].lower() in DOC_EXT else "파일"
    return "글"


def linkify(text):
    out, last = [], 0
    for m in URL.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        u = html.escape(m[0])
        out.append(f'<a href="{u}" target="_blank" rel="noopener">{u}</a>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def render_media(t, v):
    if t == "image":
        return f'<img src="{html.escape(v)}" alt="" loading="lazy">'
    if t == "video":
        return f'<video src="{html.escape(v)}" controls playsinline preload="metadata"></video>'
    if t == "youtube":
        return (f'<div class="yt"><iframe src="https://www.youtube-nocookie.com/embed/{v}" title="YouTube" referrerpolicy="strict-origin-when-cross-origin" '
                'loading="lazy" allowfullscreen allow="encrypted-media; picture-in-picture"></iframe></div>')
    name, url = v
    return f'<a class="btn file-btn" href="{html.escape(url)}">⬇ {html.escape(unquote(name))}</a>'


def render_blocks(blocks):
    """글은 body-text, 이어지는 첨부들은 하나의 media 묶음으로 (본문 순서 유지)."""
    parts, media = [], []
    for t, v in blocks + [("end", None)]:
        if t != "text" and t != "end":
            media.append(render_media(t, v))
            continue
        if media:
            parts.append(f'<div class="media">{"".join(media)}</div>')
            media = []
        if t == "text":
            parts.append(f'<div class="body-text">{linkify(v)}</div>')
    return "\n".join(parts)


def plain_text(blocks):
    return "\n\n".join(v for t, v in blocks if t == "text")


# ---------- 페이지 ----------

def kst(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(KST)


def page(title, content, root, script=""):
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&display=swap">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<header class="top"><div class="top-in">
  <a class="brand" href="{root}">daily<span>.</span>works</a>
</div></header>
<main>
{content}
</main>
{script}
</body>
</html>
"""


AVATAR = '<span class="avatar"><span class="avatar-in">{}</span></span>'.format(
    "".join(f'<i class="d{i}"></i>' for i in range(7)))

ICONS = {
    "영상": '<path d="M8 5v14l11-7z"/>',
    "이미지": '<path d="M4 5h16v14H4z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M6 17l4-5 3 3.5 2-2.5 3 4z"/>',
    "문서": '<path d="M6 3h8l4 4v14H6z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 12h6M9 16h6" stroke="currentColor" stroke-width="2"/>',
    "파일": '<path d="M16.5 6.5l-7.8 7.8a2 2 0 002.8 2.8l7.8-7.8a4 4 0 00-5.6-5.6L5.9 11.5a6 6 0 008.5 8.5l6.1-6.1" fill="none" stroke="currentColor" stroke-width="2"/>',
    "글": '<path d="M5 6h14M5 11h14M5 16h9" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
}


def icon(k):
    return f'<svg class="kind" viewBox="0 0 24 24" aria-label="{k}" role="img">{ICONS[k]}</svg>'


def date_tile(d, cls="tile-date"):
    return (f'<span class="{cls}"><span class="t-m">{d.month}월</span>'
            f'<span class="t-d">{d.day}</span><span class="t-w">{WEEKDAYS[d.weekday()]}요일</span></span>')


PROFILE_SCRIPT = """<script>
(function () {
  var W = '월화수목금토일';
  var done = new Set(JSON.parse(document.getElementById('dates').textContent));
  function kstDate(d) { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(d); }
  function shift(s, n) { var d = new Date(s + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  function wd(s) { return (new Date(s + 'T12:00:00Z').getUTCDay() + 6) % 7; }
  var today = kstDate(new Date());
  var day = done.has(today) ? today : shift(today, -1), streak = 0;
  while (done.has(day)) { streak++; day = shift(day, -1); }
  document.getElementById('streak').textContent = streak;
  var st = document.getElementById('today');
  st.textContent = done.has(today) ? '오늘 작업 업로드 완료 ✓' : '오늘의 작업은 아직이에요';
  if (done.has(today)) st.classList.add('done');
  var week = document.getElementById('week');
  for (var i = 6; i >= 0; i--) {
    var s = shift(today, -i), li = document.createElement('li');
    li.className = 'd' + wd(s) + (done.has(s) ? ' on' : '') + (s === today ? ' now' : '');
    li.innerHTML = '<span class="hl"><b></b></span><small></small>';
    li.querySelector('b').textContent = +s.slice(8);
    li.querySelector('small').textContent = s === today ? '오늘' : W[wd(s)];
    li.title = +s.slice(5, 7) + '월 ' + +s.slice(8) + '일' + (done.has(s) ? ' · 업로드함' : '');
    week.appendChild(li);
  }
})();
</script>"""

COPY_SCRIPT = """<script>
document.querySelectorAll('.copy').forEach(function (b) {
  b.addEventListener('click', function () {
    var text = Array.prototype.map.call(document.querySelectorAll('.body-text'), function (e) { return e.textContent; }).join('\\n\\n');
    navigator.clipboard.writeText(text).then(function () {
      b.textContent = '복사됨';
      setTimeout(function () { b.textContent = '복사'; }, 1500);
    });
  });
});
</script>"""


def build(issues):
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "p").mkdir(parents=True)
    shutil.copy(ROOT / "style.css", OUT / "style.css")

    tiles, dates = [], set()
    total = len(issues)
    for issue in issues:
        blocks = parse_body(issue.get("body"))
        d = kst(issue["created_at"])
        dates.add(d.date().isoformat())
        title = html.escape(issue["title"])
        n = issue["number"]
        k = kind(blocks)
        text = plain_text(blocks)

        tiles.append(
            f'<li><a class="tile d{d.weekday()}" href="p/{n}/" aria-label="{title}">'
            f'{icon(k)}{date_tile(d)}<span class="t-title">{title}</span></a></li>')

        media = [b for b in blocks if b[0] in ("image", "video", "youtube")]
        caption = [b for b in blocks if b[0] not in ("image", "video", "youtube")]
        media_html = ("".join(render_media(t, v) for t, v in media) if media
                      else date_tile(d, "post-date"))
        copy = '<button class="copy" type="button">복사</button>' if text else ""
        article = (
            f'<article class="ig-post d{d.weekday()}">'
            f'<header class="ig-head">{AVATAR}<b>daily.works</b><span class="dot">•</span>'
            f'<span class="muted">{d.month}월 {d.day}일</span>'
            f'<a class="close" href="../../" aria-label="목록으로">✕</a></header>'
            f'<div class="ig-media">{media_html}</div>'
            f'<div class="ig-caption"><div class="cap-head"><h1>{title}</h1>{copy}</div>'
            f'{render_blocks(caption)}'
            f'<time datetime="{d.date().isoformat()}">{d.year}년 {d.month}월 {d.day}일 {WEEKDAYS[d.weekday()]}요일</time>'
            f'</div></article>'
            f'<p class="back"><a href="../../">← 모든 작업 보기</a></p>')
        (OUT / "p" / str(n)).mkdir()
        (OUT / "p" / str(n) / "index.html").write_text(
            page(f"{issue['title']} · daily.works", article, "../../", COPY_SCRIPT if text else ""))

    profile = f"""<section class="profile">
  {AVATAR}
  <div class="p-info">
    <h1>daily.works</h1>
    <ul class="p-stats">
      <li>게시물 <b>{total}</b></li>
      <li>연속 <b id="streak">0</b>일</li>
      <li>기록 <b>{len(dates)}</b>일</li>
    </ul>
  </div>
  <p class="p-bio">매일 하나씩 올리는 작업 기록<br><span id="today"></span></p>
</section>
<ol class="week" id="week" aria-label="최근 7일"></ol>
<nav class="tabs"><span class="on"><svg viewBox="0 0 24 24"><path d="M3 3h18v18H3zM9 3v18M15 3v18M3 9h18M3 15h18" fill="none" stroke="currentColor" stroke-width="2"/></svg>게시물</span></nav>
<script type="application/json" id="dates">{json.dumps(sorted(dates))}</script>"""
    grid = (f'<ul class="grid">{"".join(tiles)}</ul>' if tiles
            else '<p class="empty">아직 올라온 작업물이 없어요.<br>첫 번째 작업을 올려 보세요.</p>')
    (OUT / "index.html").write_text(page("daily.works", profile + grid, "", PROFILE_SCRIPT))
    root = "/" + REPO.split("/")[-1] + "/" if REPO else "/"
    (OUT / "404.html").write_text(page("daily.works", '<p class="empty">찾는 작업물이 없어요.</p>', root))
    print(f"작업물 {total}개로 사이트를 만들었어요.")


if __name__ == "__main__":
    build(fetch_issues())
