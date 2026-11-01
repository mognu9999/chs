import json, os, random, time
import datetime as dt
from collections import Counter
import requests

TOKEN = os.environ["VK_TOKEN"]
GROUP = int(os.environ["VK_GROUP_ID"])
V = "5.199"

# ---------- Хэштеги (правьте здесь) ----------
CORE_TAGS = ["#китайский", "#китайскийязык", "#выучитькитайский"]
KIND_TAGS = {"phrase": "#фразы", "word": "#слова", "pinyin": "#пиньинь", "digest": "#повторение"}
EXTRA_TAGS = ["#оооятебяникогданепредам3", "#ии", "#искусственныйинтеллект"]

# ---------- Доли типов записей ----------
KIND_WEIGHTS = {"phrase": 45, "word": 50, "pinyin": 5}

# ---------- Воскресный пост-повторение ----------
# Выходит в воскресенье в 16:00 UTC (19:00 по Москве) вместо обычной записи.
DIGEST_HOUR_UTC = 16
DIGEST_ITEMS = 10
FORCE_DIGEST = os.environ.get("FORCE_DIGEST") == "1"


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


def wait_for_minute_ending_9():
    """Ждём ближайшую минуту :09, :19, :29, :39, :49 или :59 (максимум около 10 минут)."""
    while True:
        t = dt.datetime.now(dt.timezone.utc)
        if t.minute % 10 == 9:
            return
        time.sleep(60.5 - t.second - t.microsecond / 1e6)


if os.environ.get("NO_WAIT") != "1":
    wait_for_minute_ending_9()

phrases = load("phrases.json", [])
history = load("history.json", [])
by_zh = {p["zh"]: p for p in phrases}
now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
week_ago = now - dt.timedelta(days=7)


def ts(h):
    return dt.datetime.fromisoformat(h["ts"])


recent = Counter(h["zh"] for h in history if ts(h) > week_ago)
total = Counter(h["zh"] for h in history)


def pick():
    kind = random.choices(list(KIND_WEIGHTS), weights=list(KIND_WEIGHTS.values()))[0]
    allowed = [p for p in phrases if recent[p["zh"]] < 2]
    pool = [p for p in allowed if p["kind"] == kind] or allowed or phrases
    least = min(total[p["zh"]] for p in pool)
    return random.choice([p for p in pool if total[p["zh"]] == least])


def tags_for(kind):
    return " ".join(CORE_TAGS + [KIND_TAGS.get(kind, "")] + EXTRA_TAGS).replace("  ", " ")


def lesson_lines(p):
    """Примеры урока по строкам: иероглиф — пиньинь — перевод (если части совпадают по числу)."""
    zh = p["zh"].split()
    py = [x.strip() for x in p["pinyin"].split("·")] if "·" in p["pinyin"] else p["pinyin"].split()
    ru = [x.strip() for x in p["ru"].split("·")]
    if len(zh) == len(py) == len(ru):
        return "\n".join(f"{a} — {b} — {c}" for a, b, c in zip(zh, py, ru))
    return None


def build_message(p):
    if p["kind"] == "pinyin":
        parts = [f"🔤 Урок пиньиня: {p.get('title', 'произношение')}"]
        lines = lesson_lines(p)
        parts.append(lines or "\n\n".join([p["zh"], p["pinyin"], p["ru"]]))
    else:
        parts = [p["zh"], p["pinyin"], p["ru_sound"], p["ru"]]
    if p.get("note"):
        parts.append("💡 " + p["note"])
    parts.append(tags_for(p["kind"]))
    return "\n\n".join(parts)


def digest_items():
    """Слова и фразы, опубликованные за неделю (без уроков пиньиня), по порядку выхода."""
    seen, items = set(), []
    for h in history:
        if ts(h) <= week_ago or h["zh"] in seen:
            continue
        p = by_zh.get(h["zh"])
        if p and p["kind"] in ("phrase", "word"):
            seen.add(h["zh"])
            items.append(p)
    if len(items) > DIGEST_ITEMS:
        keep = set(random.sample(range(len(items)), DIGEST_ITEMS))
        items = [p for i, p in enumerate(items) if i in keep]
    return items


def digest_due():
    if FORCE_DIGEST:
        return True
    if now.weekday() != 6 or now.hour < DIGEST_HOUR_UTC:
        return False
    already = any(h.get("kind") == "digest" and ts(h) > now - dt.timedelta(days=6) for h in history)
    return not already


def build_digest(items):
    lines = [f'{i}. {p["zh"]} — {p["pinyin"]} — {p["ru"]}' for i, p in enumerate(items, 1)]
    return "\n\n".join([
        "📚 Повторяем слова недели",
        "\n".join(lines),
        "✍️ Закройте правую часть и вспомните перевод. Сколько получилось? Напишите в комментариях!",
        tags_for("digest"),
    ])


items = digest_items() if digest_due() else []
if len(items) >= 3:
    api("wall.post", owner_id=-GROUP, from_group=1, message=build_digest(items))
    entry = {"zh": f"digest-{now:%Y-%m-%d}", "kind": "digest", "ts": now.isoformat()}
else:
    p = pick()
    api("wall.post", owner_id=-GROUP, from_group=1, message=build_message(p))
    entry = {"zh": p["zh"], "kind": p["kind"], "ts": now.isoformat()}

history.append(entry)
history = history[-3000:]
with open("history.json", "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=1)
