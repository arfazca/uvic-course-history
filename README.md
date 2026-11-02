# uvic-course-history

when was a UVic eng/CS course last offered, and which semester does it usually come back in?
reads the public banner timetable (same one uvic's own page uses), no login, answers local.

clone and run it:

```bash
git clone https://github.com/arfazca/uvic-course-history.git
cd uvic-course-history
bash run.sh
```

windows: `run.bat` instead of `bash run.sh`.

first run downloads ten-ish years of term data, takes a few minutes, one time. after that everything is instant and offline. `bash run.sh` opens the report page in your browser.

for a specific course, same line plus the course:

```bash
bash run.sh CSC 370
bash run.sh CSC 421 432 436 438   # batch works too
```

more useful commands:

```bash
python3 uvic_courses.py sync           # pull new terms (fast, incremental)
python3 uvic_courses.py list SENG      # every SENG course, last offered + odds
python3 uvic_courses.py stale          # courses that quietly stopped appearing
python3 uvic_courses.py desc CSC 105   # calendar description + prereq link
python3 uvic_courses.py report         # rebuild report.html
```
