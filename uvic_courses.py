#!/usr/bin/env python3
"""
uvic course offering history / predictor — read live data straight from UVic's
public Banner search (banner.uvic.ca/StudentRegistrationSsb). No login needed.

  sync        fetch & cache per-term section lists     default subjects: CSC, ECE, SENG
  check       history + seasonal pattern + forecast    e.g.  check CSC 370 ECE 260
  list        one-line summary per course in a subject e.g.  list SENG
  stale       courses that look like they stopped being offered
  desc        calendar description link for a course   e.g.  desc CSC 105
  terms       which terms are cached
  report      self-contained HTML timeline/forecast page
"""

import argparse
import concurrent.futures
import http.cookiejar
import html as htmlesc
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

BASE = "https://banner.uvic.ca/StudentRegistrationSsb/ssb"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SEASON_BY_MM = {"01": "SPR", "05": "SUM", "09": "FALL"}
SEASON_LABEL = {"FALL": "Fall (Sep-Dec)", "SPR": "Spring (Jan-Apr)", "SUM": "Summer (May-Aug)"}
SEASON_CHIP = {"FALL": "Fall", "SPR": "Spring", "SUM": "Summer"}
DEFAULT_SUBJECTS = ["CSC", "ECE", "SENG"]
PROJ = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE = os.path.join(PROJ, "data", "offerings.json")
DEFAULT_REPORT = os.path.join(PROJ, "report.html")


# --- Banner session ---------------------------------------------------------

class Banner:
    """One cookie-jar session against banner.uvic.ca."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", UA), ("Accept", "application/json, text/html, */*")]

    def _call(self, path, params=None, method="GET", tries=2):
        url = BASE + path if path.startswith("/") else path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        err = None
        for _ in range(tries):
            try:
                req = urllib.request.Request(url, method=method,
                                             data=b"" if method == "POST" else None)
                with self.op.open(req, timeout=45) as r:
                    return r.read().decode("utf-8", "replace")
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
                err = e
                time.sleep(1.5)
        raise RuntimeError(f"{path}: {err}")

    def terms(self):
        raw = self._call("/classSearch/getTerms", {"searchTerm": "", "offset": 1, "max": 99})
        return {t["code"]: t.get("description", "") for t in json.loads(raw) if t.get("code")}

    def new_term(self, code):
        self._call("/classSearch/resetDataForm")
        self._call("/term/search",
                   {"term": code, "studyPath": "", "studyPathText": "",
                    "startDatepicker": "", "endDatepicker": ""}, method="POST")

    def sections(self, term, subject, page_max=500):
        got, page, total = [], 1, None
        while True:
            raw = self._call("/searchResults/searchResults", {
                "txt_term": term, "txt_subject": subject, "txt_courseNumber": "",
                "txt_credithourlow": "", "txt_credithourhigh": "",
                "startDatepicker": "", "endDatepicker": "",
                "pageOffset": page, "pageMaxSize": page_max,
                "sortColumns": "subjectDescription", "sortDirections": "ascending",
            })
            body = json.loads(raw)
            if not body.get("success"):
                break
            data = body.get("data") or []
            total = body.get("totalCount", total)
            got.extend(data)
            if not data or len(data) < page_max or (total is not None and len(got) >= total):
                break
            page += 1
        return got

    @staticmethod
    def trim(s):
        keys = ("courseReferenceNumber", "sequenceNumber", "courseNumber", "courseTitle",
                "creditHours", "scheduleTypeDescription", "instructionalMethodDescription",
                "campusDescription", "maximumEnrollment", "enrollment", "waitCount",
                "waitAvailable")
        return {k: s.get(k) for k in keys if s.get(k) is not None}

    def fetch_term(self, term, subjects):
        per = {}
        for subj in subjects:
            # banner materializes the search set once per session: reset + rebind
            # before every subject or the later subjects will replay the first set
            self._call("/classSearch/resetDataForm")
            self._call("/term/search",
                       {"term": term, "studyPath": "", "studyPathText": "",
                        "startDatepicker": "", "endDatepicker": ""}, method="POST")
            secs = self.sections(term, subj)
            if secs:
                per[subj] = [self.trim(x) for x in secs]
            time.sleep(0.1)
        return per

    def course_description(self, term, crn):
        raw = self._call("/searchResults/getCourseDescription",
                         {"term": term, "courseReferenceNumber": crn})
        url = re.search(r'href="(https?://[^"]*uvic\.ca[^"]*)"', raw)
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^.*?course description[^.]*\.\s*", "", text, flags=re.I)
        return {"url": url.group(1) if url else None, "text": text[:500]}


# --- cache ------------------------------------------------------------------

def load_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                sys.exit(f"not valid json: {path} — delete it and re-run sync")
    return {"fetched_at": None, "term_desc": {}, "terms": {}}


def save_cache(cache, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cache["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))


def active_terms(cache):
    """terms where at least one synced subject actually had sections"""
    return sorted(t for t, v in cache["terms"].items() if v["subjects"])


def sync_cmd(a):
    cache = load_cache(a.cache)
    subjects = [s.strip().upper() for s in a.subjects.split(",") if s.strip()]
    banner = Banner()
    all_terms = banner.terms()
    cache.setdefault("term_desc", {}).update(all_terms)

    if a.term:
        wanted = [t.strip() for t in a.term.split(",") if t.strip()]
    else:
        cutoff = f"{date.today().year - a.years}01" if a.years else None
        wanted = [c for c in all_terms
                  if c[4:6] in SEASON_BY_MM and (cutoff is None or c >= cutoff)]
    wanted.sort()

    todo = [t for t in wanted if a.refresh or t not in cache["terms"]]
    if not todo:
        print(f"cache already covers {len(cache['terms'])} terms "
              f"({', '.join(active_terms(cache)[-3:])})")
        return

    def work(term):
        b = banner if a.jobs <= 1 else Banner()
        return term, b.fetch_term(term, subjects)

    done = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        futs = {pool.submit(work, t): t for t in todo}
        for fut in concurrent.futures.as_completed(futs):
            term = futs[fut]
            try:
                _, per = fut.result()
            except (RuntimeError, KeyError) as e:
                failed += 1
                print(f"  ! {term} failed: {e}", file=sys.stderr)
                continue
            counts = ", ".join(f"{k} {len(v)}" for k, v in sorted(per.items())) or "(nothing published)"
            cache["terms"][term] = {"subjects": per, "desc": all_terms.get(term, "")}
            done += 1
            print(f"  {term}  {counts}")
            if done % 5 == 0:
                save_cache(cache, a.cache)

    save_cache(cache, a.cache)
    print(f"synced {done}/{len(todo)} terms ({failed} failed) -> {a.cache}")


# --- aggregation & analysis --------------------------------------------------

def season(code):
    return SEASON_BY_MM.get(code[4:6], "?")


def next_term_codes(after_code, n=3):
    y, mm = int(after_code[:4]), after_code[4:6]
    cyc = [(y, "01"), (y, "05"), (y, "09")]
    seq = sorted(cyc + [(y + 1, "01"), (y + 1, "05"), (y + 1, "09")])
    out, seen = [], False
    for yy, m in seq:
        c = f"{yy}{m}"
        if c > after_code:
            seen = True
            out.append(c)
        if seen and len(out) >= n:
            break
    return out


def term_label(code):
    names = {"01": "Spring", "05": "Summer", "09": "Fall"}
    return f"{names.get(code[4:6], '?')} {code[:4]}"


def aggregate(cache):
    """(subject, number) -> {title, terms[chronological]} — glosses over section rows."""
    out = {}
    for term in active_terms(cache):
        for subj, secs in cache["terms"][term]["subjects"].items():
            for s in secs:
                num = str(s.get("courseNumber", "")).zfill(3)
                key = (subj, num)
                c = out.setdefault(key, {"title": s.get("courseTitle"), "terms": []})
                c["title"] = c["title"] or s.get("courseTitle")
                if term not in c["terms"]:
                    c["terms"].append(term)
    return out


def analyze(key, agg, win=8):
    terms = sorted(agg[key]["terms"])
    now = date.today().year
    yrs = {str(y) for y in range(now + 1 - win, now + 1)}
    per_season = {}
    for t in terms:
        if t[:4] in yrs:
            per_season.setdefault(season(t), set()).add(t[:4])
    probs = {s: round(len(per_season.get(s, set())) / len(yrs), 2) for s in ("FALL", "SPR", "SUM")}
    return key, {
        "title": agg[key]["title"], "terms": terms, "last": terms[-1],
        "probs": probs, "perf": per_season,
    }


def course_view(subj, num, agg, all_active, win):
    key = (subj, num)
    if key not in agg:
        return None
    _, r = analyze(key, agg, win)
    last = r["last"]
    gap = [t for t in all_active if t > last]
    future_listed = last > max(all_active) if all_active else False
    return {
        "key": key, "title": r["title"], "terms": r["terms"], "last": last,
        "probs": r["probs"], "gap_terms": gap, "future_listed": future_listed,
        "regular": len(r["terms"]) >= 3,
    }


# --- output helpers ----------------------------------------------------------

def parse_specs(tokens):
    """accepts: CSC CSC 370 csc370 CSC-370 -> [(CSC,370)...]"""
    joined = " ".join(tokens)
    parts = re.split(r"[,\s]+", joined.upper())
    specs, i = [], 0
    while i < len(parts):
        t = parts[i]
        m = re.fullmatch(r"([A-Z]{2,4})(\d{1,3}[A-Z]?)", t)
        if m:
            specs.append((m.group(1), m.group(2).zfill(3)))
            i += 1
            continue
        m = re.fullmatch(r"([A-Z]{2,4})", t)
        if m and i + 1 < len(parts) and re.fullmatch(r"\d{1,3}[A-Z]?", parts[i + 1]):
            specs.append((m.group(1), parts[i + 1].zfill(3)))
            i += 2
            continue
        i += 1
    return specs


def timeline(terms, codes, mark="#", none="·"):
    return "".join(mark if t in terms else none for t in codes)


def check_cmd(a):
    cache = load_cache(a.cache)
    agg = aggregate(cache)
    active = active_terms(cache)
    if not active:
        print("cache empty — run: uvic_courses.py sync")
        return
    upcoming = next_term_codes(max(active), 3)

    specs = parse_specs(a.course)
    if not specs:
        print("could not parse course ids; e.g. check CSC 370 or CSC370")
        return
    for subj, num in specs:
        v = course_view(subj, num, agg, active, a.window)
        if v is None:
            print(f"\n{subj} {num}: not found in cache (subject not synced, or never offered in window). "
                  f"try:  list {subj}")
            continue
        probs = v["probs"]
        chips = "  ".join(f"{k} {pct:.0%}" for k, pct in
                          (("Fall", probs["FALL"]), ("Spring", probs["SPR"]), ("Summer", probs["SUM"])))
        print(f"\n{subj} {num}  {v['title'] or ''}")
        print(f"  seen {len(v['terms'])} term(s) · last offered {term_label(v['last'])}")
        if v["future_listed"]:
            print(f"  ALREADY LISTED in the published timetable for {term_label(v['last'])}")
        elif v["gap_terms"]:
            print(f"  !!! expected-but-missing for {len(v['gap_terms'])} straight published term(s)")
        print(f"  seasonal frequency over last {a.window}y:  {chips}")
        print(f"  timeline [{active[0]} … {active[-1]}]:")
        print(f"              {timeline(set(v['terms']), active)}")
        print("  forecast:")
        for code in upcoming:
            if code in active:
                verdict = "listed" if code in v["terms"] else "not listed"
                print(f"    {term_label(code):<16} {verdict}")
            else:
                p = probs[season(code)]
                tag = "LIKELY" if p >= 0.8 else ("possible" if p >= 0.45 else "unlikely")
                print(f"    {term_label(code):<16} {tag:<8} {int(round(p * 100)):>3}%  ({SEASON_LABEL[season(code)]})")


def terms_cmd(a):
    cache = load_cache(a.cache)
    for t in sorted(cache["terms"]):
        per = cache["terms"][t]["subjects"]
        counts = ", ".join(f"{k} {len(v)}" for k, v in sorted(per.items())) or "-"
        gap = "" if per else "   (no data — not published or empty)"
        print(f"{t}  {term_label(t):<16} {counts}{gap}")


# --- main -------------------------------------------------------------------

def main(argv):
    p = argparse.ArgumentParser(
        prog="uvic_courses",
        description="UVic course offering history & next-term forecast, from banner.uvic.ca")
    p.add_argument("--cache", default=DEFAULT_CACHE)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync")
    s.add_argument("--subjects", default=",".join(DEFAULT_SUBJECTS))
    s.add_argument("--years", type=int, default=10)
    s.add_argument("--term", help="comma-separated term codes override (e.g. 202609,202701)")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--jobs", type=int, default=3)

    c = sub.add_parser("check")
    c.add_argument("course", nargs="+")
    c.add_argument("--window", type=int, default=8)

    sub.add_parser("terms")

    a = p.parse_args(argv)
    dispatch = {"sync": sync_cmd, "check": check_cmd, "terms": terms_cmd}
    dispatch[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv[1:])
