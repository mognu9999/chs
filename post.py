import json, math, os, random, sys, time
import datetime as dt
from collections import Counter
import requests

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
DRY_RUN = os.environ.get("DRY_RUN") == "1"

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

# ---------- Ограничение длины ----------
MAX_LINES = 14
LINE_WIDTH = 46

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def log(*a):
    print(*a, flush=True)


def send_telegram(text):
    url = "https://" + "api.telegram.org" + f"/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    last = None
    for attempt in range(5):
        try:
            r = requests.post(url, json=payload, timeout=30).json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
            continue
        if r.get("ok"):
            return r["result"]
        retry_after = r.get("parameters", {}).get("retry_after")
        if retry_after and attempt < 4:
            time.sleep(int(retry_after) + 1)
            continue
        raise RuntimeError(f"Telegram API error: {r}")
    raise RuntimeError(f"Telegram API недоступен: {last}")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)


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
    first_tag = next(iter(CORE_TAGS))
    if level == 0:
        t = CORE_TAGS + [kt] + EXTRA_TAGS
    elif level == 1:
        t = [first_tag, kt, BRAND_TAG]
    else:
        t = [first_tag, BRAND_TAG]
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


phrases = load("phrases.json", [])
history = load("history.json", [])
by_zh = {p["zh"]: p for p in phrases}


def ts(h):
    return dt.datetime.fromisoformat(h["ts"])


def save_history():
    global history
    history = history[-3000:]
    tmp = "history.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    os.replace(tmp, "history.json")


def digest_for(now):
    if not (now.weekday() == 6 and now.hour >= DIGEST_HOUR_UTC):
        return None
    day = f"{now:%Y-%m-%d}"
    done = [h for h in history if h.get("kind") == "digest" and h["zh"].startswith(f"digest-{day}-")]
    if len(done) >= DIGEST_PARTS:
        return None
    used = {z for h in done for z in h.get("items", [])}
    week_ago = now - dt.timedelta(days=7)
    seen, items = set(), []
    for h in history:
        if h.get("kind") == "digest" or not (week_ago < ts(h) <= now) or h["zh"] in seen:
            continue
        p = by_zh.get(h["zh"])
        if p and p["kind"] in ("phrase", "word"):
            seen.add(h["zh"])
            items.append(p)
    random.Random(day).shuffle(items)
    pool = [p for p in items if p["zh"] not in used]
    if len(pool) < 2:
        return None
    part = len(done) + 1
    message, ids = build_digest(pool, part)
    return message, {"zh": f"digest-{day}-{part}", "kind": "digest", "ts": now.isoformat(), "items": ids}


def regular_for(now):
    week_ago = now - dt.timedelta(days=7)
    recent = Counter(h["zh"] for h in history if ts(h) > week_ago)
    total = Counter(h["zh"] for h in history)
    kind = next(iter(random.choices(list(KIND_WEIGHTS), weights=list(KIND_WEIGHTS.values()))))
    allowed = [p for p in phrases if recent[p["zh"]] < 2]
    pool = [p for p in allowed if p["kind"] == kind] or allowed or phrases
    least = min(total[p["zh"]] for p in pool)
    p = random.choice([x for x in pool if total[x["zh"]] == least])
    return build_message(p), {"zh": p["zh"], "kind": p["kind"], "ts": now.isoformat()}


def compose(now):
    return digest_for(now) or regular_for(now)


def main():
    now = utcnow()
    if history and (now - ts(history[-1])) < dt.timedelta(minutes=110):
        log(f"Пост уже выходил в {history[-1]['ts']} UTC, пропускаю.")
        return

    message, entry = compose(now)
    if DRY_RUN:
        log(f"[DRY] {now} -> {entry['kind']} {entry['zh']}")
    else:
        send_telegram(message)
        log(f"✅ Опубликовано в Telegram ({now} UTC): {entry['zh']}")
    history.append(entry)
    if not DRY_RUN:
        save_history()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ Ошибка: {e!r}")
        sys.exit(1)
