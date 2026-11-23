import json, math, os, random
import datetime as dt
from collections import Counter
import requests

TOKEN = os.environ["VK_TOKEN"]
GROUP = int(os.environ["VK_GROUP_ID"])
V = "5.199"

# ---------- Настройки времени (UTC) ----------
TARGET_MINUTE = 39  # Во сколько минут должен выйти пост (например, :39)

# ---------- Хэштеги ----------
BRAND_TAG = "#оооятебяникогданепредам3"
CORE_TAGS = ["#китайский", "#китайскийязык", "#выучитькитайский"]
KIND_TAGS = {"phrase": "#фразы", "word": "#слова", "pinyin": "#пиньинь", "digest": "#повторение"}
EXTRA_TAGS = [BRAND_TAG, "#ии", "#искусственныйинтеллект"]

# ---------- Доли типов записей ----------
KIND_WEIGHTS = {"phrase": 45, "word": 50, "pinyin": 5}

# ---------- Воскресное повторение ----------
DIGEST_HOUR_UTC = 16
DIGEST_PARTS = 3
DIGEST_MAX_ITEMS = 5
FORCE_DIGEST = os.environ.get("FORCE_DIGEST") == "1"

# ---------- Ограничение VK ----------
MAX_LINES = 14
LINE_WIDTH = 46

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def api(method, **params):
    params.update(access_token=TOKEN, v=V)
    r = requests.post(f"https://vk.com{method}", data=params, timeout=30).json()
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
by_zh = {p["zh"]: p for p in phrases}

now_utc = dt.datetime.now(dt.timezone.utc)
now = now_utc.replace(tzinfo=None)

# Защита от дублей: если за последние 90 минут уже был пост (не в ручном режиме), выходим
if history and os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
    last_ts = dt.datetime.fromisoformat(history[-1]["ts"])
    if (now - last_ts).total_seconds() < 90 * 60:
        print("Пост за этот интервал уже опубликован. Пропускаем.")
        exit(0)

# Вычисляем точное время публикации (ближайшие :39 минут в будущем)
target_dt = now_utc.replace(minute=TARGET_MINUTE, second=0, microsecond=0)
if target_dt <= now_utc + dt.timedelta(seconds=30):
    # Если GitHub задержал запуск и :39 уже прошли, публикуем немедленно
    publish_ts = None
    record_ts = now
else:
    # Ставим в отложку ВК ровно на :39:00
    publish_ts = int(target_dt.timestamp())
    record_ts = target_dt.replace(tzinfo=None)

week_ago = record_ts - dt.timedelta(days=7)


def ts(h):
    return dt.datetime.fromisoformat(h["ts"])


recent = Counter(h["zh"] for h in history if ts(h) > week_ago)
total = Counter(h["zh"] for h in history)


# ---------- Вёрстка с учётом лимита строк ----------
def width(s):
    return sum(2 if "\u4e00" <= c <= "\u9fff" else 1 for c in s)


def est_lines(text):
    return sum(max(1, math.ceil(width(line) / LINE_WIDTH)) for line in text.split("\n"))


def pick_fitting(candidates):
    for c in candidates:
        if est_lines(c) <= MAX_LINES:
            return c
    return candidates[-1]


def tags(kind, level):
    kt = KIND_TAGS.get(kind, "")
    if level == 0:
        t = CORE_TAGS + [kt] + EXTRA_TAGS
    elif level == 1:
        t = [CORE_TAGS[0], kt, BRAND_TAG]
    else:
        t = [CORE_TAGS[0], BRAND_TAG]
    return " ".join(x for x in t if x)


def join(*parts, sep="\n\n"):
    return sep.join(x for x in parts if x)


def block(n, zh, py, snd, ru):
    return "\n".join([f"{CIRCLED[n - 1]} {zh}", py, snd, ru])


def compact_item(n, zh, py, snd, ru):
    first = f"{CIRCLED[n - 1]} {zh} · {py} · {snd}"
    if width(first) <= LINE_WIDTH:
        return first + "\n" + ru
    return f"{CIRCLED[n - 1]} {zh}\n{py} · {snd}\n{ru}"


def build_word_or_phrase(p):
    kind = p["kind"]
    four = [p["zh"], p["pinyin"], p["ru_sound"], p["ru"]]
    note = ("💡 " + p["note"]) if p.get("note") else ""
    cands = []
    for body in ("\n\n".join(four), "\n".join(four)):
        for lvl in (0, 1, 2):
            cands.append(join(body, note, tags(kind, lvl)))
    cands.append(join("\n".join(four), tags(kind, 2)))
    return pick_fitting(cands)


def lesson_items(p):
    zh = p["zh"].split()
    py = [x.strip() for x in p["pinyin"].split("·")]
    snd = [x.strip() for x in p["ru_sound"].split("·")]
    ru = [x.strip() for x in p["ru"].split("·")]
    if len(zh) == len(py) == len(snd) == len(ru):
        return list(zip(zh, py, snd, ru))
    return None


def build_lesson(p):
    header = f"🔤 Урок пиньиня: {p.get('title', 'произношение')}"
    note = ("💡 " + p["note"]) if p.get("note") else ""
    items = lesson_items(p)
    if not items:
        return join(header, p["zh"], p["pinyin"], p["ru"], note, tags("pinyin", 2))
    blocks = [block(i, *t) for i, t in enumerate(items, 1)]
    compact = [compact_item(i, *t) for i, t in enumerate(items, 1)]
    cands = []
    for body, sep in (("\n\n".join(blocks), "\n\n"), ("\n\n".join(compact), "\n\n"),
                      ("\n".join(compact), "\n\n"), ("\n".join(compact), "\n")):
        for lvl in (1, 2):
            cands.append(join(header, body, note, tags("pinyin", lvl), sep=sep))
    return pick_fitting(cands)


def build_message(p):
    return build_lesson(p) if p["kind"] == "pinyin" else build_word_or_phrase(p)


# ---------- Воскресное повторение ----------
def digest_state():
    today = f"{record_ts:%Y-%m-%d}"
    done = [h for h in history if h.get("kind") == "digest" and h["zh"].startswith(f"digest-{today}-")]
    return today, done


def digest_pool(done):
    used = {z for h in done for z in h.get("items", [])}
    seen, items = set(), []
    for h in history:
        if h.get("kind") == "digest" or ts(h) <= week_ago or h["zh"] in seen:
            continue
        p = by_zh.get(h["zh"])
        if p and p["kind"] in ("phrase", "word"):
            seen.add(h["zh"])
            items.append(p)
    random.Random(f"{record_ts:%Y-%m-%d}").shuffle(items)
    return [p for p in items if p["zh"] not in used]


def compose_digest(chosen, part):
    header = f"📚 Повторяем слова недели · {part}/{DIGEST_PARTS}"
    body = "\n\n".join(compact_item(i, p["zh"], p["pinyin"], p["ru_sound"], p["ru"])
                       for i, p in enumerate(chosen, 1))
    prompt = "✍️ Закройте перевод и вспомните его."
    cands = [join(header, body, prompt, tags("digest", lvl)) for lvl in (1, 2)]
    return pick_fitting(cands)


def build_digest(pool, part):
    chosen = []
    for p in pool:
        trial = chosen + [p]
        if chosen and est_lines(compose_digest(trial, part)) > MAX_LINES:
            break
        chosen = trial
        if len(chosen) >= DIGEST_MAX_ITEMS:
            break
    return compose_digest(chosen, part), [p["zh"] for p in chosen]


# ---------- Выбор записи ----------
def pick():
    kind = random.choices(list(KIND_WEIGHTS), weights=list(KIND_WEIGHTS.values()))[0]
    allowed = [p for p in phrases if recent[p["zh"]] < 2]
    pool = [p for p in allowed if p["kind"] == kind] or allowed or phrases
    least = min(total[p["zh"]] for p in pool)
    return random.choice([p for p in pool if total[p["zh"]] == least])


today, done = digest_state()
digest_due = (FORCE_DIGEST or (record_ts.weekday() == 6 and record_ts.hour >= DIGEST_HOUR_UTC)) and len(done) < DIGEST_PARTS
pool = digest_pool(done) if digest_due else []

post_params = {"owner_id": -GROUP, "from_group": 1}
if publish_ts:
    post_params["publish_date"] = publish_ts

if len(pool) >= 2:
    part = len(done) + 1
    message, item_ids = build_digest(pool, part)
    api("wall.post", message=message, **post_params)
    entry = {"zh": f"digest-{today}-{part}", "kind": "digest", "ts": record_ts.isoformat(), "items": item_ids}
else:
    p = pick()
    api("wall.post", message=build_message(p), **post_params)
    entry = {"zh": p["zh"], "kind": p["kind"], "ts": record_ts.isoformat()}

history.append(entry)
history = history[-3000:]
with open("history.json", "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=1)
