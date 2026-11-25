"""Бот для VK (работает с токеном СООБЩЕСТВА, публикует сразу, без отложки).

Идея: слоты публикации — каждые 2 часа в :SLOT_MINUTE по UTC. Workflow запускается несколько
раз вокруг каждого слота, а скрипт сам решает, что делать:
  * слот вот-вот наступит (до WAIT_MAX_MIN минут) -> дожидается точной минуты и публикует;
  * слот уже прошёл, но запись не вышла (GitHub опоздал) -> публикует сразу, если опоздание
    не больше MAX_LATE_MIN минут;
  * слот уже закрыт (есть в history.json или на стене группы) -> ничего не делает.
Один слот = ровно одна запись, пропущенный запуск GitHub добирается следующим.
"""
import json, math, os, random, sys, time
import datetime as dt
from collections import Counter
import requests

TOKEN = os.environ["VK_TOKEN"]
GROUP = int(os.environ["VK_GROUP_ID"])
V = "5.199"
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# ---------- Расписание (всё по UTC; Москва = UTC+3) ----------
SLOT_MINUTE = 29          # минута публикации: 29 (можно поставить 19)
SLOT_EVERY_HOURS = 2      # шаг между постами
SLOT_HOUR_OFFSET = 0      # 0 -> 00:29, 02:29 UTC ... (в Москве 03:29, 05:29 ...); 1 -> нечётные часы UTC
WAIT_MAX_MIN = 4          # если до слота не больше стольких минут — ждём его и публикуем вовремя
MAX_LATE_MIN = 100        # если слот пропущен, но прошло не больше стольких минут — публикуем сразу

# ---------- Хэштеги (правьте здесь) ----------
BRAND_TAG = "#оооятебяникогданепредам3"
CORE_TAGS = ["#китайский", "#китайскийязык", "#выучитькитайский"]
KIND_TAGS = {"phrase": "#фразы", "word": "#слова", "pinyin": "#пиньинь", "digest": "#повторение"}
EXTRA_TAGS = [BRAND_TAG, "#ии", "#искусственныйинтеллект"]

# ---------- Доли типов записей ----------
KIND_WEIGHTS = {"phrase": 45, "word": 50, "pinyin": 5}

# ---------- Воскресное повторение ----------
# Слоты воскресенья с 16:00 UTC (19:00 по Москве): первые три подряд — повторение недели.
DIGEST_HOUR_UTC = 16
DIGEST_PARTS = 3
DIGEST_MAX_ITEMS = 5

# ---------- Ограничение VK ----------
# В ленте VK показывает первые 14 строк записи без вложений, остальное прячет под «Показать ещё».
MAX_LINES = 14
LINE_WIDTH = 46

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def log(*a):
    print(*a, flush=True)


class VKError(RuntimeError):
    def __init__(self, err):
        super().__init__(err)
        self.code = err.get("error_code") if isinstance(err, dict) else None


def api(method, **params):
    """Вызов VK API с повторами при сетевых сбоях и временных ошибках."""
    params.update(access_token=TOKEN, v=V)
    last = None
    for attempt in range(5):
        try:
            r = requests.post(f"https://api.vk.com/method/{method}", data=params, timeout=30).json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
            continue
        if "error" in r:
            if r["error"].get("error_code") in (1, 6, 9, 10) and attempt < 4:  # временные
                last = VKError(r["error"])
                time.sleep(3 * (attempt + 1))
                continue
            raise VKError(r["error"])
        return r["response"]
    raise RuntimeError(f"VK API недоступен: {last}")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)


# ---------- Вёрстка с учётом лимита строк ----------
def width(s):
    return sum(2 if "\u4e00" <= c <= "\u9fff" else 1 for c in s)


def est_lines(text):
    return sum(max(1, math.ceil(width(line) / LINE_WIDTH)) for line in text.split("\n"))


def pick_fitting(candidates):
    """Первый вариант, который помещается в лимит строк; иначе самый компактный."""
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
    """Полный блок: 4 строки, как в обычной записи."""
    return "\n".join([f"{CIRCLED[n - 1]} {zh}", py, snd, ru])


def compact_item(n, zh, py, snd, ru):
    """Компактный блок: 2 строки (3, если не помещается)."""
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


# ---------- Состояние ----------
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


# ---------- Выбор записи для конкретного слота ----------
def digest_for(slot):
    """Возвращает (message, entry) для воскресного повторения или None."""
    if not (slot.weekday() == 6 and slot.hour >= DIGEST_HOUR_UTC):
        return None
    day = f"{slot:%Y-%m-%d}"
    done = [h for h in history if h.get("kind") == "digest" and h["zh"].startswith(f"digest-{day}-")]
    if len(done) >= DIGEST_PARTS:
        return None
    used = {z for h in done for z in h.get("items", [])}
    week_ago = slot - dt.timedelta(days=7)
    seen, items = set(), []
    for h in history:
        if h.get("kind") == "digest" or not (week_ago < ts(h) <= slot) or h["zh"] in seen:
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
    return message, {"zh": f"digest-{day}-{part}", "kind": "digest", "ts": slot.isoformat(), "items": ids}


def regular_for(slot):
    week_ago = slot - dt.timedelta(days=7)
    recent = Counter(h["zh"] for h in history if ts(h) > week_ago)
    total = Counter(h["zh"] for h in history)
    kind = random.choices(list(KIND_WEIGHTS), weights=list(KIND_WEIGHTS.values()))[0]
    allowed = [p for p in phrases if recent[p["zh"]] < 2]
    pool = [p for p in allowed if p["kind"] == kind] or allowed or phrases
    least = min(total[p["zh"]] for p in pool)
    p = random.choice([x for x in pool if total[x["zh"]] == least])
    return build_message(p), {"zh": p["zh"], "kind": p["kind"], "ts": slot.isoformat()}


def compose(slot):
    return digest_for(slot) or regular_for(slot)


# ---------- Слоты ----------
def exact_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def slot_candidates(now):
    """Слоты вокруг «сейчас», от более раннего к более позднему."""
    day = now.replace(hour=0, minute=0, second=0) - dt.timedelta(days=1)
    out = []
    for d in range(3):
        for h in range(24):
            if (h - SLOT_HOUR_OFFSET) % SLOT_EVERY_HOURS == 0:
                out.append((day + dt.timedelta(days=d)).replace(hour=h, minute=SLOT_MINUTE, second=0))
    return out


def pick_slot(now):
    """Слот, который нужно обработать прямо сейчас, либо None."""
    soonest = None
    for s in slot_candidates(now):
        if now <= s <= now + dt.timedelta(minutes=WAIT_MAX_MIN):
            return s                      # скоро наступит — ждём и публикуем точно
        if s < now <= s + dt.timedelta(minutes=MAX_LATE_MIN):
            soonest = s                   # уже прошёл, но недавно
    return soonest


def already_done(slot):
    if any(ts(h) == slot for h in history):
        return True
    # Страховка: если history.json не успел сохраниться, смотрим на стену группы.
    try:
        r = api("wall.get", owner_id=-GROUP, count=10)
        lo = int((slot - dt.timedelta(minutes=5)).replace(tzinfo=dt.timezone.utc).timestamp())
        return any(it["date"] >= lo and it.get("from_id") == -GROUP for it in r["items"])
    except VKError as e:
        log(f"⚠️ Не удалось проверить стену ({e.args[0] if e.args else e}); опираюсь на history.json")
        return False


def main():
    now = exact_now()
    slot = pick_slot(now)
    if slot is None:
        log(f"{now} UTC: сейчас не время публикации, выхожу.")
        return
    if already_done(slot):
        log(f"{now} UTC: слот {slot} уже опубликован, выхожу.")
        return
    message, entry = compose(slot)
    if now < slot:
        wait = (slot - now).total_seconds()
        log(f"Жду до {slot} UTC ({wait:.0f} с)")
        if not DRY_RUN:
            time.sleep(wait)
    elif (now - slot).total_seconds() > 60:
        log(f"Слот {slot} пропущен на {(now - slot).seconds // 60} мин, публикую сразу")
    if DRY_RUN:
        log(f"[DRY] {slot} -> {entry['kind']} {entry['zh']}")
        return
    try:
        api("wall.post", owner_id=-GROUP, from_group=1, message=message)
    except VKError as e:
        if e.code in (5, 15, 27):
            log("Токен не подходит. Нужен ключ доступа СООБЩЕСТВА с правом «Управление сообществом» и доступом к стене "
                "(Управление → Работа с API → Ключи доступа).")
        raise
    history.append(entry)
    save_history()
    log(f"✅ Опубликовано (слот {slot} UTC): {entry['zh']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ Ошибка: {e!r}")
        sys.exit(1)
