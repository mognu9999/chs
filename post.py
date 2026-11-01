import json, os, random, html
import datetime as dt
from collections import Counter
import requests, cairosvg

TOKEN = os.environ["VK_TOKEN"]
GROUP = int(os.environ["VK_GROUP_ID"])
V = "5.199"

def api(method, **params):
    params.update(access_token=TOKEN, v=V)
    r = requests.post(f"https://api.vk.com/method/{method}", data=params, timeout=30).json()
    if "error" in r:
        raise RuntimeError(r["error"])
    return r["response"]

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

phrases = load("phrases.json", [])
history = load("history.json", [])
now = dt.datetime.utcnow()
week_ago = now - dt.timedelta(days=7)

recent = Counter(h["zh"] for h in history if dt.datetime.fromisoformat(h["ts"]) > week_ago)
total = Counter(h["zh"] for h in history)

def pick():
    kind = random.choices(["phrase", "word"], [6, 4])[0]
    pool = [p for p in phrases if p["kind"] == kind and recent[p["zh"]] < 2]
    if not pool:
        pool = [p for p in phrases if recent[p["zh"]] < 2]
    least = min(total[p["zh"]] for p in pool)
    return random.choice([p for p in pool if total[p["zh"]] == least])

def make_png(zh, path):
    n = 8  # максимум иероглифов в строке
    lines = [zh[i:i + n] for i in range(0, len(zh), n)]
    size = min(260, int(900 / max(len(l) for l in lines)), int(800 / len(lines)))
    lh = size * 1.25
    y0 = 540 - (len(lines) - 1) * lh / 2 + size * 0.35
    text = "".join(
        f'<text x="540" y="{y0 + i * lh:.0f}" font-size="{size}" text-anchor="middle" '
        f'font-family="Noto Sans CJK SC, Noto Sans SC, sans-serif" font-weight="700" fill="#000">'
        f'{html.escape(l)}</text>'
        for i, l in enumerate(lines)
    )
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080">'
           f'<rect width="1080" height="1080" fill="#fff"/>{text}</svg>')
    with open("last.svg", "w", encoding="utf-8") as f:
        f.write(svg)
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=path, output_width=1080)

p = pick()
make_png(p["zh"], "last.png")

srv = api("photos.getWallUploadServer", group_id=GROUP)
with open("last.png", "rb") as f:
    up = requests.post(srv["upload_url"], files={"photo": f}, timeout=60).json()
saved = api("photos.saveWallPhoto", group_id=GROUP,
            photo=up["photo"], server=up["server"], hash=up["hash"])[0]
attachment = f"photo{saved['owner_id']}_{saved['id']}"

message = f'{p["zh"]}\n\n{p["pinyin"]}\n\n{p["ru_sound"]}\n\n{p["ru"]}\n\n#китайский #китайскийязык'
api("wall.post", owner_id=-GROUP, from_group=1, message=message, attachments=attachment)

history.append({"zh": p["zh"], "kind": p["kind"], "ts": now.isoformat()})
history = history[-3000:]
with open("history.json", "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=1)
