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
# Studio collections shown on the Food & nutrition and Cyclical self-care pages (newest first)
LIBRARY = {
    "recipes": "68ff6beadaf7fece197e7e60",
    "rituals": "68fb89347310583c6573a719",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    "JANUAR FEBRUAR MARS APRIL MAI JUNI JULI AUGUST SEPTEMBER OKTOBER NOVEMBER DESEMBER".split())}
OUT = Path(__file__).resolve().parent.parent / "website" / "classes.js"
THUMBS = OUT.parent / "img" / "cls"   # local copies of the thumbnails, so pages can show them without loading another site


def save_thumb(vid, url):
    """Download a class thumbnail and store a small WebP copy; returns the site-relative path or None."""
    dest = THUMBS / f"{vid}.webp"
    if dest.exists():
        return f"img/cls/{vid}.webp"
    try:
        from io import BytesIO
        from PIL import Image
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sjelehagen-sync)"})
        im = Image.open(BytesIO(urllib.request.urlopen(req, timeout=30).read())).convert("RGB")
        im.thumbnail((480, 270))
        THUMBS.mkdir(parents=True, exist_ok=True)
        im.save(dest, "WEBP", quality=72, method=6)
        return f"img/cls/{vid}.webp"
    except Exception as exc:  # keep the sync going; pages fall back to the remote thumbnail
        print("thumbnail failed", vid, exc)
        return None


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
                "tlocal": save_thumb(v["_id"], THUMB.format(id=v["_id"], v=ver)),
                "url": f"{STUDIO}/video/{v['_id']}/{slugify(v.get('name', ''))}?playlist={sched['_id']}&program={program}",
            }
    days.append(current)
    days = (days + [None] * 7)[:7]
    return {"week": sched.get("name", "").strip(), "url": STUDIO + path, "days": days}


def thumb_url(v, ver):
    """Prefer a custom thumbnail uploaded in Instill (smallest size), else the auto video frame."""
    t = v.get("thumbnails")
    if isinstance(t, dict) and t:
        def width(k):
            m = re.match(r"(\d+)x", k)
            return int(m.group(1)) if m else 99999
        return t[sorted(t, key=width)[0]]
    return THUMB.format(id=v["_id"], v=ver)


def build_library(pid, limit=6):
    videos, _ = collect(fetch_state(f"/playlist/{pid}"))
    out = []
    for v in list(videos.values())[:limit]:
        changed = v.get("thumbnailTimeChangedAt") or v.get("updatedAt") or v.get("createdAt")
        ver = int(dt.datetime.fromisoformat(changed.replace("Z", "+00:00")).timestamp()) if changed else 0
        thumb = thumb_url(v, ver)
        out.append({
            "id": v["_id"],
            "name": re.sub(r"\s+", " ", v.get("name", "")).strip(),
            "min": max(1, round((v.get("duration") or 0) / 60)),
            "desc": first_sentence(v.get("description")),
            "thumb": thumb,
            "tlocal": save_thumb(v["_id"], thumb),
            "url": f"{STUDIO}/video/{v['_id']}/{slugify(v.get('name', ''))}?playlist={pid}",
        })
    return out


def main():
    today = dt.date.today()
    data = {"updated": today.isoformat()}
    for key, path in PLANS.items():
        data[key] = build_plan(key, path, today)
        print(key, data[key]["week"], [d["name"] if d else "hvile" for d in data[key]["days"]])
    lib = {}
    for key, pid in LIBRARY.items():
        try:
            lib[key] = build_library(pid)
            print(key, [x["name"] for x in lib[key]])
        except Exception as exc:  # the weekly plans matter most; never fail the sync over a collection
            print("library failed", key, exc)
    js = "/* Generated by tools/sync_instill.py, do not edit by hand */\nwindow.SH_PLANS = " + \
        json.dumps(data, ensure_ascii=False, indent=1) + ";\nwindow.SH_LIBRARY = " + \
        json.dumps(lib, ensure_ascii=False, indent=1) + ";\n"
    OUT.write_text(js, encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    sys.exit(main())
