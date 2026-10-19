"""저장소 주인이 쓴 열린 Issue 들을 읽어 정적 사이트(_site/)를 만든다. (GitHub Actions 에서 실행)

- Issue 하나 = 작업물 하나 (제목 = Issue 제목, 글·첨부 = Issue 본문)
- 날짜 = Issue 를 만든 날짜(한국 시간). 수정해도 바뀌지 않고, 수정 시각은 표시하지 않는다.
- 닫힌 Issue, 다른 사람이 쓴 Issue 는 사이트에 나오지 않는다.
- 본문은 글자 그대로 보여준다(프롬프트가 깨지지 않도록). 첨부 이미지·영상·파일·유튜브 주소만 미디어로 바꾼다.
"""
import hashlib
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


# ---------- GitHub ----------

def api_list(path):
    """GitHub API 목록을 모든 페이지에 걸쳐 가져온다."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "daily-works"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        url = f"https://api.github.com/repos/{REPO}/{path}{sep}per_page=100&page={page}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
            batch = json.load(resp)
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def fetch_issues():
    owner = REPO.split("/")[0]
    issues = [i for i in api_list(f"issues?state=open&creator={owner}") if "pull_request" not in i]
    for issue in issues:
        # 댓글은 저장소 주인 계정으로 단 것만 (공개 저장소라 누구나 댓글을 달 수 있으므로)
        issue["_comments"] = ([c for c in api_list(f"issues/{issue['number']}/comments")
                               if c["user"]["login"] == owner] if issue.get("comments") else [])
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

HANDLE = "todaki.daily"          # 계정명
DISPLAY_NAME = "토닥이"           # 표시 이름
BIO = "혼자 사는 대학생 토끼의 소소한 하루 🐰"
TEAM = "11조 · 30days project"

# 하이라이트에 보일 팀원: (예전 이니셜 — 댓글 "C:" 호환용, 하이라이트 색)
# Issue 에 같은 이름의 라벨을 붙이면 그 사람의 개인 작업으로 모인다.
MEMBERS = {
    "박채민": ("C", "#C89595"),  # 더스티 로즈
    "김나연": ("N", "#9EAF9F"),  # 세이지 그린
    "정윤근": ("Y", "#AEA0B5"),  # 뮤트 라벤더
    "박진혁": ("J", "#C5AD8D"),  # 웜 베이지
}


def kst(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(KST)


# 디자인 파일 내용이 바뀌면 주소도 바뀌게 해서, 브라우저가 예전 파일을 쓰지 않도록 한다.
CSS_VERSION = hashlib.sha1((ROOT / "style.css").read_bytes()).hexdigest()[:10]

# 프로필 사진: 저장소에 profile.jpg / profile.png 등을 넣으면 쓴다.
PROFILE_IMAGE = next((f for ext in ("jpg", "jpeg", "png", "webp")
                      if (f := ROOT / f"profile.{ext}").exists()), None)
PROFILE_VERSION = hashlib.sha1(PROFILE_IMAGE.read_bytes()).hexdigest()[:10] if PROFILE_IMAGE else ""


def page(title, content, root, script=""):
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="{root}style.css?v={CSS_VERSION}">
</head>
<body>
<header class="top"><div class="top-in">
  <a class="brand" href="{root}">{HANDLE}</a>
</div></header>
<main>
{content}
</main>
{script}
</body>
</html>
"""


def avatar(root, cls=""):
    """토닥이 프로필 사진 (없으면 임시로 토끼 이모지)."""
    if PROFILE_IMAGE:
        inner = f'<img src="{root}{PROFILE_IMAGE.name}?v={PROFILE_VERSION}" alt="{DISPLAY_NAME}">'
    else:
        inner = '<span class="avatar-ph" aria-hidden="true">🐰</span>'
    return f'<span class="avatar {cls}">{inner}</span>'


def member_dot(name, cls=""):
    """조원 하이라이트 색 동그라미 (글자 없이 단색)."""
    return f'<span class="mdot {cls}" style="--mc:{MEMBERS[name][1]}" aria-hidden="true"></span>'


PLAY = '<span class="play" aria-label="영상"><svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z"/></svg></span>'


def tile_media(blocks):
    """피드 썸네일: 본문의 첫 이미지·영상·유튜브. 없으면 None (날짜 썸네일을 쓴다)."""
    for t, v in blocks:
        if t == "image":
            return f'<img class="t-media" src="{html.escape(v)}" alt="" loading="lazy">', False
        if t == "video":
            return (f'<video class="t-media" src="{html.escape(v)}#t=0.5" muted playsinline '
                    'preload="metadata" tabindex="-1"></video>'), True
        if t == "youtube":
            return f'<img class="t-media" src="https://i.ytimg.com/vi/{v}/hqdefault.jpg" alt="" loading="lazy">', True
    return None, False


def date_tile(d, cls="tile-date"):
    return (f'<span class="{cls}"><span class="t-m">{d.month}월</span>'
            f'<span class="t-d">{d.day}</span><span class="t-w">{WEEKDAYS[d.weekday()]}요일</span></span>')


FILTER_SCRIPT = """<script>
(function () {
  var tiles = document.querySelectorAll('.grid > li');
  var buttons = document.querySelectorAll('.highlights button');
  var current = null;
  function apply() {
    tiles.forEach(function (li) {
      li.hidden = !!current && (li.dataset.members || '').split(' ').indexOf(current) < 0;
    });
    buttons.forEach(function (b) {
      var on = b.dataset.member === current;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on);
    });
  }
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      current = current === b.dataset.member ? null : b.dataset.member;
      history.replaceState(null, '', current ? '#' + current : location.pathname);
      apply();
    });
  });
  var fromHash = decodeURIComponent(location.hash.slice(1));
  buttons.forEach(function (b) { if (b.dataset.member === fromHash) current = fromHash; });
  apply();
})();
</script>"""

PROFILE_SCRIPT = """<script>
(function () {
  var done = new Set(JSON.parse(document.getElementById('dates').textContent));
  function kstDate(d) { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(d); }
  function shift(s, n) { var d = new Date(s + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  var today = kstDate(new Date());
  var day = done.has(today) ? today : shift(today, -1), streak = 0;
  while (done.has(day)) { streak++; day = shift(day, -1); }
  document.getElementById('streak').textContent = streak;
  document.getElementById('today').textContent = done.has(today)
    ? '오늘 하루도 여기 적어 뒀어요 🌿'
    : '오늘의 이야기는 천천히 준비 중이에요';
})();
</script>"""

COPY_SCRIPT = """<script>
document.querySelectorAll('.copy').forEach(function (b) {
  b.addEventListener('click', function () {
    var text = Array.prototype.map.call(document.querySelectorAll('.ig-caption > .body-text'), function (e) { return e.textContent; }).join('\\n\\n');
    navigator.clipboard.writeText(text).then(function () {
      b.textContent = '복사됨';
      setTimeout(function () { b.textContent = '복사'; }, 1500);
    });
  });
});
</script>"""


# 댓글 맨 앞 "채민:", "박채민:", "C:", "[채민]" 등으로 작성자를 정한다. 없으면 토닥이.
_ALIASES = {}
for _name, (_initial, _color) in MEMBERS.items():
    for _alias in (_name, _name[1:], _initial, _initial.lower()):
        _ALIASES[_alias] = _name
_ALIAS_RE = "|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True))
COMMENT_AUTHOR = re.compile(rf"^\s*(?:\[({_ALIAS_RE})\]|({_ALIAS_RE})\s*[:：])[ \t]*\n?")


def comment_author(body):
    m = COMMENT_AUTHOR.match(body or "")
    if not m:
        return None, body or ""
    return _ALIASES[m[1] or m[2]], body[m.end():]


def render_comments(comments, root):
    items = []
    for c in comments:
        who, body = comment_author(c.get("body"))
        blocks = parse_body(body)
        if not blocks:
            continue
        if who:
            av, name = member_dot(who, "sm"), html.escape(who)
        else:
            av, name = avatar(root, "sm"), HANDLE
        items.append(f'<li class="comment">{av}<div class="c-body"><b class="c-name">{name}</b>'
                     f'{render_blocks(blocks)}</div></li>')
    return f'<ul class="comments">{"".join(items)}</ul>' if items else ""


def render_attachments(blocks):
    """기획서 같은 첨부 파일은 본문 아래에 작게."""
    files = [v for t, v in blocks if t == "file"]
    if not files:
        return ""
    links = "".join(f'<li><a href="{html.escape(url)}">📎 {html.escape(unquote(name))}</a></li>'
                    for name, url in files)
    return f'<ul class="attach">{links}</ul>'


def build(issues):
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "p").mkdir(parents=True)
    shutil.copy(ROOT / "style.css", OUT / "style.css")
    if PROFILE_IMAGE:
        shutil.copy(PROFILE_IMAGE, OUT / PROFILE_IMAGE.name)

    tiles, dates = [], set()
    total = len(issues)
    for issue in issues:
        blocks = parse_body(issue.get("body"))
        d = kst(issue["created_at"])
        dates.add(d.date().isoformat())
        title = html.escape(issue["title"])
        n = issue["number"]
        text = plain_text(blocks)

        # 이름 라벨이 붙은 글 = 그 사람의 개인 작업, 없으면 토닥이(11조 공동) 게시물
        members = [m for m in MEMBERS if m in {l.get("name") for l in issue.get("labels", [])}]
        names = html.escape(", ".join(members))
        solo_badge = (f'<span class="solo-badge">{"".join(member_dot(m, "xs") for m in members)}'
                      f'{names}</span>') if members else ""
        date_chip = f'<span class="date-chip">{d.month}.{d.day:02d}</span>'

        thumb, is_video = tile_media(blocks)
        if thumb:
            tile = (f'<a class="tile has-media" href="p/{n}/" aria-label="{title}">'
                    f'{thumb}{PLAY if is_video else ""}{solo_badge}{date_chip}</a>')
        else:
            tile = (f'<a class="tile" href="p/{n}/" aria-label="{title}">'
                    f'{solo_badge}{date_tile(d)}<span class="t-title">{title}</span></a>')
        tiles.append(f'<li data-members="{html.escape(" ".join(members))}">{tile}</li>')

        if members:
            head_who = "".join(member_dot(m, "sm") for m in members) + f"<b>{names}</b>"
            solo_tag = '<span class="solo-tag">개인 작업</span>'
        else:
            head_who, solo_tag = f'{avatar("../../", "sm")}<b>{HANDLE}</b>', ""
        media = [b for b in blocks if b[0] in ("image", "video", "youtube")]
        caption = [b for b in blocks if b[0] == "text"]
        media_html = ("".join(render_media(t, v) for t, v in media) if media
                      else date_tile(d, "post-date"))
        copy = '<button class="copy" type="button">복사</button>' if text else ""
        article = (
            f'<article class="ig-post">'
            f'<header class="ig-head">{head_who}{solo_tag}'
            f'<a class="close" href="../../" aria-label="목록으로">✕</a></header>'
            f'<div class="ig-media">{media_html}</div>'
            f'<div class="ig-caption"><div class="cap-head"><h1>{title}</h1>{copy}</div>'
            f'{render_blocks(caption)}'
            f'<time datetime="{d.date().isoformat()}">{d.year}년 {d.month}월 {d.day}일 {WEEKDAYS[d.weekday()]}요일</time>'
            f'{render_comments(issue.get("_comments", []), "../../")}'
            f'{render_attachments(blocks)}'
            f'</div></article>'
            f'<p class="back"><a href="../../">← 토닥이의 하루 모아보기</a></p>')
        (OUT / "p" / str(n)).mkdir()
        (OUT / "p" / str(n) / "index.html").write_text(
            page(f"{issue['title']} · {DISPLAY_NAME} ({HANDLE})", article, "../../", COPY_SCRIPT if text else ""))

    highlights = "".join(
        f'<li><button type="button" data-member="{m}" aria-pressed="false">'
        f'<span class="hl" style="--mc:{color}"></span><small>{m}</small></button></li>'
        for m, (_, color) in MEMBERS.items())
    profile = f"""<section class="profile">
  {avatar("")}
  <div class="p-info">
    <h1>{HANDLE}</h1>
    <p class="p-stats">게시물 {total} · 연속 <span id="streak">0</span>일</p>
  </div>
  <div class="p-bio">
    <b class="p-name">{DISPLAY_NAME}</b>
    <p>{BIO}</p>
    <p class="p-today" id="today"></p>
    <p class="p-team">{TEAM}</p>
  </div>
</section>
<ul class="highlights" aria-label="조원별 작업 보기">{highlights}</ul>
<nav class="tabs"><span class="on"><svg viewBox="0 0 24 24"><path d="M3 3h18v18H3zM9 3v18M15 3v18M3 9h18M3 15h18" fill="none" stroke="currentColor" stroke-width="2"/></svg>게시물</span></nav>
<script type="application/json" id="dates">{json.dumps(sorted(dates))}</script>"""
    grid = (f'<ul class="grid">{"".join(tiles)}</ul>' if tiles
            else '<p class="empty">아직 올라온 이야기가 없어요.</p>')
    (OUT / "index.html").write_text(
        page(f"{DISPLAY_NAME} ({HANDLE})", profile + grid, "", PROFILE_SCRIPT + FILTER_SCRIPT))
    root = "/" + REPO.split("/")[-1] + "/" if REPO else "/"
    (OUT / "404.html").write_text(page(HANDLE, '<p class="empty">찾는 이야기가 없어요.</p>', root))
    print(f"게시물 {total}개로 사이트를 만들었어요.")


if __name__ == "__main__":
    build(fetch_issues())
