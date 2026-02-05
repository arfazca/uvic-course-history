# uvic-course-history

"Was it offered in fall or spring? When will CSC 432 come back?"

Answers for Engineering & Computer Science courses, read straight from UVic's
public Banner timetable search (`banner.uvic.ca/StudentRegistrationSsb`),
the same engine behind the official
[online timetable](https://www.uvic.ca/students/academics/course-timetable/index.php).
No login, no scraping of course-outline portals.

## Use it

```bash
cd uvic-course-history

# 1. build the cache (first time: ~10 years of terms, CSC + ECE + SENG)
python3 uvic_courses.py sync

# later: refresh with new terms only (incremental)
python3 uvic_courses.py sync --years 3
python3 uvic_courses.py sync --term 202709,202801        # or specific terms
python3 uvic_courses.py sync --term 202609 --refresh     # re-pull one term

# 2. ask about any course
python3 uvic_courses.py check CSC 370
python3 uvic_courses.py check CSC 432 436 438   # planning your 4th year? check a batch
python3 uvic_courses.py check csc490            # formats: "CSC 370", "CSC370", "CSC-370"

# 3. browse / hunt
python3 uvic_courses.py list SENG               # full subject table, last-offered + freq
python3 uvic_courses.py list ECE --sort last
python3 uvic_courses.py stale                   # courses that quietly vanished
python3 uvic_courses.py stale --gap 6

# 4. visual
python3 uvic_courses.py report && open report.html
```

`desc` shows the calendar description + prereq page link banner serves for a section:

```bash
python3 uvic_courses.py desc CSC 105
```

## What `check` tells you

```
CSC 370  Database Systems
  seen 9 term(s) · last offered Spring 2026
  seasonal frequency over last 8y:  Fall 100%  Spring 100%  Summer 12%
  timeline [201709 … 202701]:
              ##·#·#·#·#·#·#·#####...
  forecast:
    Summer 2027     unlikely     12%  (Summer (May-Aug))
    Fall 2027       LIKELY  100%  (Fall (Sep-Dec))
    ...
```

- **last offered**: most recent term with any section
- **seasonal frequency**: share of the last 8 years it appeared in Fall / Spring / Summer;
  that's the "usually fall + spring" answer
- **timeline**: `#` = offered that term (chronological date order)
- **forecast**: the next ≥3 terms: either a probability
  (what history says) or the actual published data when the term is already out
- courses missing from ≥4 straight terms while having been regular get flagged
  `!!! expected-but-missing` (usually means sabbatical, faculty change, or it's dormant)

## Foreground vs reality

- Percentages are inferences from history, **not official UVic scheduling**.
- UVic publishes the **Winter** timetable (Fall + Spring) mid-May and the
  **Summer** timetable mid-February: so "already listed" chips appear when
  reality beats prediction; a re-`sync` refreshes them.
- Banner keeps old terms in the search dropdown, so the cache can cover
  ~2016 → today (34 terms here).
- `ECE` codes only appear in Banner from 2018-05 onward in these searches
  (the electrical/computer course codes were reshuffled when the ECE programs
  launched their current numberings): so ECE histories start there.
- Cross-listed courses can appear under two codes; check both
  (e.g., CSC 4xx ↔ ECE 5xx topic courses).

## How it works

`sync` replays exactly what the browser does against Banner's SSB endpoints:

1. `GET /classSearch/getTerms` → every term code (2016+ … next year)
2. `POST /term/search?term=…` → bind the term to the session
3. `GET /searchResults/searchResults?txt_term=…&txt_subject=CSC&…` (paginated)
   → JSON sections with CRN, seats, waitlist, units

Everything is stored trimmed in `data/offerings.json` (~MBs) and then
aggregated to per-course term lists, so `check`/`list`/`report` are instant
and offline-friendly.

Files: `uvic_courses.py` (CLI), `data/offerings.json` (cache), `report.html` (UI).

Cache format (trimmed):

```json
{"202409": {"subjects": {"CSC": [{"courseReferenceNumber": "10667", "courseNumber": "105", ...}]}}, ...}
```

## Adding other departments

```bash
python3 uvic_courses.py sync --term 202701 --refresh   # cached subjects stay cached
python3 uvic_courses.py sync --subjects CSC,MATH,STAT,ECE,SENG,BIOL   # any subject codes
```

(First full sync with new subjects only fetches missing terms; cached terms for
a subject survive unless you pass `--refresh`.)

Hint: subject codes are the same 2–4 letter codes used in registration
(`CSC`, `ECE`, `SENG`, `ELEC`, `MECH`, ...). To see every one:
`banner.uvic.ca` → "Search for classes" → subject `%` search.
