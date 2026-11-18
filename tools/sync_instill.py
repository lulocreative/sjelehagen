#!/usr/bin/env python3
"""Fetch this week's classes from the public Instill weekly-plan pages and write website/classes.js.

The quiz (website/index*.html) and the starter-plan email use this file to show the real
classes: name, length, thumbnail and a direct link to the class in the studio.
Run: python3 tools/sync_instill.py   (a GitHub Action runs it every night)
"""
import datetime as dt
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

STUDIO = "https://studio.sjelehagen.no"
THUMB = "https://dhxp8qydz3sp6.cloudfront.net/video/{id}/640x360.png?v={v}"
PLANS = {
    "energi": "/challenge/6908f4a8088b74318d3796db/ukentlige-planer-energi-kraft",
    "ro": "/challenge/6908fa85e431f4a7fdb02b5d/ukentlig-planer-ro-flyt",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    "JANUAR FEBRUAR MARS APRIL MAI JUNI JULI AUGUST SEPTEMBER OKTOBER NOVEMBER DESEMBER".split())}
OUT = Path(__file__).resolve().parent.parent / "website" / "classes.js"


def fetch_state(path):
    req = urllib.request.Request(STUDIO + path, headers={"User-Agent": "Mozilla/5.0 (sjelehagen-sync)"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    blob = re.search(r'<meta name="nullstack" content="(%7B[^"]+)"', html).group(1)
    return json.loads(urllib.parse.unquote(blob))


def collect(state):
    videos, schedules = {}, []
    def walk(o):
        if isinstance(o, dict):
            if "_id" in o and "duration" in o:
                videos[o["_id"]] = o
            if o.get("type") == "schedule" and "_id" in o:
                schedules.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(state)
    uniq = {s["_id"]: s for s in schedules}
    return videos, list(uniq.values())


def week_range(name, today):
    """'ENERGI 16 - 22 NOVEMBER' / 'LAV INTENSITET 26 OKTOBER - 1 NOVEMBER' -> (start, end)."""
    m = re.search(r"(\d{1,2})\s*([A-ZÆØÅ]+)?\s*-\s*(\d{1,2})\s*([A-ZÆØÅ]+)", name.upper())
    if not m:
        return None
    d1, m1, d2, m2 = m.groups()
    mo2 = MONTHS.get(m2)
    mo1 = MONTHS.get(m1) if m1 else mo2
    if not mo1 or not mo2:
        return None
    y = today.year
    end = dt.date(y, mo2, int(d2))
    start = dt.date(y if mo1 <= mo2 else y - 1, mo1, int(d1))
    if (start - today).days > 180:  # e.g. a December plan seen in January
        start, end = start.replace(year=y - 1), end.replace(year=y - 1)
    return start, end


def pick_schedule(schedules, today):
    dated = [(week_range(s.get("name", ""), today), s) for s in schedules]
    dated = [(r, s) for r, s in dated if r]
    for (start, end), s in dated:
        if start <= today <= end:
            return s
    past = [(r, s) for r, s in dated if r[0] <= today]
    if past:
        return max(past, key=lambda x: x[0][0])[1]
    return schedules[0] if schedules else None


def slugify(name):
    s = name.strip().lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def first_sentence(text, limit=140):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    t = re.split(r"(?<=[.!?])\s", t)[0] if t else ""
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "…"


def build_plan(key, path, today):
    state = fetch_state(path)
    videos, schedules = collect(state)
    sched = pick_schedule(schedules, today)
    if not sched:
        raise RuntimeError(f"no weekly schedule found for {key}")
    program = path.split("/")[2]
    days, current, started = [], None, False
    for item in sched.get("videos", []):
        if isinstance(item, dict):  # day marker (MANDAG, TIRSDAG, ...); a day without a class is a rest day
            if started:
                days.append(current)
            started, current = True, None
            continue
        v = videos.get(item)
        if v and current is None:
            changed = v.get("thumbnailTimeChangedAt") or v.get("updatedAt") or v.get("createdAt")
            ver = int(dt.datetime.fromisoformat(changed.replace("Z", "+00:00")).timestamp()) if changed else 0
            current = {
                "id": v["_id"],
                "name": v.get("name", "").strip(),
                "min": max(1, round((v.get("duration") or 0) / 60)),
                "desc": first_sentence(v.get("description")),
                "thumb": THUMB.format(id=v["_id"], v=ver),
                "url": f"{STUDIO}/video/{v['_id']}/{slugify(v.get('name', ''))}?playlist={sched['_id']}&program={program}",
            }
    days.append(current)
    days = (days + [None] * 7)[:7]
    return {"week": sched.get("name", "").strip(), "url": STUDIO + path, "days": days}


def main():
    today = dt.date.today()
    data = {"updated": today.isoformat()}
    for key, path in PLANS.items():
        data[key] = build_plan(key, path, today)
        print(key, data[key]["week"], [d["name"] if d else "hvile" for d in data[key]["days"]])
    js = "/* Generated by tools/sync_instill.py, do not edit by hand */\nwindow.SH_PLANS = " + \
        json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    OUT.write_text(js, encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    sys.exit(main())
