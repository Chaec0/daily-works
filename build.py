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
        return (f'<div class="yt"><iframe src="https://www.youtube-nocookie.com/embed/{v}" title="YouTube" '
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
<header class="top">
  <a class="brand" href="{root}">
    <span class="brand-dots" aria-hidden="true">{"".join(f'<i class="d{i}"></i>' for i in range(7))}</span>
    daily<span>/</span>works
  </a>
</header>
<main>
{content}
</main>
{script}
</body>
</html>
"""


HERO_SCRIPT = """<script>
(function () {
  var W = '월화수목금토일', DAYS = 28;
  var done = new Set(JSON.parse(document.getElementById('dates').textContent));
  function kstDate(d) { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(d); }
  function shift(s, n) { var d = new Date(s + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  function wd(s) { return (new Date(s + 'T12:00:00Z').getUTCDay() + 6) % 7; }
  var today = kstDate(new Date()), p = today.split('-');
  var day = done.has(today) ? today : shift(today, -1), streak = 0;
  while (done.has(day)) { streak++; day = shift(day, -1); }
  var hero = document.getElementById('hero');
  hero.className = 'hero d' + wd(today);
  document.getElementById('h-md').textContent = +p[1] + '.' + p[2];
  document.getElementById('h-yw').textContent = p[0] + ' · ' + W[wd(today)] + '요일';
  var st = document.getElementById('h-status');
  st.textContent = done.has(today) ? '오늘 작업 업로드 완료 ✓' : '오늘의 작업을 아직 올리지 않았어요';
  if (done.has(today)) st.classList.add('done');
  document.getElementById('h-streak').textContent = streak;
  var strip = document.getElementById('h-strip');
  for (var i = DAYS - 1; i >= 0; i--) {
    var s = shift(today, -i), li = document.createElement('li');
    li.className = 'd' + wd(s) + (done.has(s) ? ' on' : '') + (s === today ? ' today' : '');
    li.title = +s.slice(5, 7) + '월 ' + +s.slice(8) + '일 (' + W[wd(s)] + ')' + (done.has(s) ? ' · 업로드함' : '');
    strip.appendChild(li);
  }
  hero.hidden = false;
})();
</script>"""

COPY_SCRIPT = """<script>
document.querySelectorAll('.copy').forEach(function (b) {
  b.addEventListener('click', function () {
    var text = Array.prototype.map.call(document.querySelectorAll('.body-text'), function (e) { return e.textContent; }).join('\n\n');
    navigator.clipboard.writeText(text).then(function () {
      b.textContent = '복사됨';
      setTimeout(function () { b.textContent = '복사'; }, 1500);
    });
  });
});
</script>"""


def stamp(d, big=False):
    if big:
        return (f'<div class="stamp big"><span class="stamp-m">{d.year}.{d.month}</span>'
                f'<span class="stamp-d">{d.day}</span><span class="stamp-w">{WEEKDAYS[d.weekday()]}요일</span></div>')
    return (f'<div class="stamp"><span class="stamp-m">{d.month}월</span>'
            f'<span class="stamp-d">{d.day}</span><span class="stamp-w">{WEEKDAYS[d.weekday()]}</span></div>')


def card_preview(blocks):
    for t, v in blocks:
        if t == "image":
            return f'<img class="thumb" src="{html.escape(v)}" alt="" loading="lazy">'
        if t == "video":
            return f'<video class="thumb" src="{html.escape(v)}#t=0.1" muted playsinline preload="metadata"></video>'
        if t == "youtube":
            return f'<img class="thumb" src="https://i.ytimg.com/vi/{v}/hqdefault.jpg" alt="" loading="lazy">'
    for t, v in blocks:
        if t == "file":
            return f'<p class="file-chip">{html.escape(unquote(v[0]))}</p>'
    return ""


def build(issues):
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "p").mkdir(parents=True)
    shutil.copy(ROOT / "style.css", OUT / "style.css")

    cards, dates = [], set()
    total = len(issues)
    for idx, issue in enumerate(issues):
        blocks = parse_body(issue.get("body"))
        d = kst(issue["created_at"])
        dates.add(d.date().isoformat())
        title = html.escape(issue["title"])
        n = issue["number"]
        text = plain_text(blocks)
        snip = re.sub(r"\s+", " ", text).strip()
        snip = snip[:140] + ("…" if len(snip) > 140 else "")

        cards.append(
            f'<li><a class="card d{d.weekday()}" href="p/{n}/">{stamp(d)}<div class="card-body">'
            f'<div class="meta"><span class="no">No.{total - idx}</span><span class="badge">{kind(blocks)}</span></div>'
            f'<h2>{title}</h2>{f"<p class=snippet>{html.escape(snip)}</p>" if snip else ""}'
            f'{card_preview(blocks)}</div></a></li>')

        copy = '<button class="btn ghost copy" type="button">복사</button>' if text else ""
        article = (
            f'<article class="post d{d.weekday()}"><header class="post-head">{stamp(d, big=True)}<div>'
            f'<div class="meta"><time datetime="{d.date().isoformat()}">'
            f'{d.year}년 {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]}) 업로드</time>'
            f'<span class="badge">{kind(blocks)}</span></div><h1>{title}</h1></div></header>'
            f'<div class="body">{copy}{render_blocks(blocks)}</div>'
            f'<footer class="actions"><a class="btn" href="../../">← 목록</a></footer></article>')
        (OUT / "p" / str(n)).mkdir()
        (OUT / "p" / str(n) / "index.html").write_text(
            page(f"{issue['title']} · Daily Works", article, "../../", COPY_SCRIPT if text else ""))

    hero = """<section class="hero" id="hero" hidden>
  <div class="hero-date"><span class="hero-label">오늘</span><strong id="h-md"></strong><span id="h-yw"></span></div>
  <div class="hero-status">
    <p class="status" id="h-status"></p>
    <p class="stats"><b id="h-streak">0</b>일 연속 · 총 <b>%d</b>일 · <b>%d</b>개의 작업</p>
    <ol class="strip" id="h-strip" aria-label="최근 28일 기록"></ol>
  </div>
</section>
<script type="application/json" id="dates">%s</script>""" % (len(dates), total, json.dumps(sorted(dates)))
    feed = (f'<ul class="feed">{"".join(cards)}</ul>' if cards
            else '<p class="empty">아직 올라온 작업물이 없어요.<br>첫 번째 작업을 올려 보세요.</p>')
    (OUT / "index.html").write_text(page("Daily Works", hero + feed, "", HERO_SCRIPT))
    root = "/" + REPO.split("/")[-1] + "/" if REPO else "/"
    (OUT / "404.html").write_text(page("Daily Works", '<p class="empty">찾는 작업물이 없어요.</p>', root))
    print(f"작업물 {total}개로 사이트를 만들었어요.")


if __name__ == "__main__":
    build(fetch_issues())
