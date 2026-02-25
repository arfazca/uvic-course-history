#!/usr/bin/env python3
#uvic courses: what was last offered, when does it come back
# talks to banner.uvic.ca (the same ssb backend the official timetable uses), no login
#
#  sync      grab per-term section lists (csc, ece, seng default)
#  check     one course or a batch  -> last offered, seasonal odds, forecast
#  list      every course in a subject
#   stale     courses that quietly stopped appearing
#  desc COUR calendar description banner serves
#  terms     cached terms
#  report    rebuilds report.html

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
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36" ) # banner chokes on urllib's default UA for some reason
SEASON_BY_MM={"01" :"SPR" , "05" : "SUM","09":"FALL" }
SEASON_LABEL = {"FALL" : "Fall (Sep-Dec)" , "SPR" : "Spring (Jan-Apr)","SUM":"Summer (May-Aug)" }
SEASON_CHIP ={"FALL" : "Fall", "SPR":"Spring" , "SUM": "Summer" }


DEFAULT_SUBJECTS = ["CSC", "ECE", "SENG"]
PROJ =os.path.dirname( os.path.abspath(__file__))
DEFAULT_CACHE =os.path.join (PROJ , "data","offerings.json" )
DEFAULT_REPORT =os.path.join (PROJ , "report.html")

# term codes are YYYYMM, 01=spring 05=summer 09=fall
# the zero padding is what makes plain string sort == chronological sort


class Banner:
    #one cookiejar session. banner materializes a search result set per session,
    # more on that in fetch_term, cost me an afternoon
    def __init__ ( self) :

        self.jar =http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent",UA ) ,("Accept" , "application/json, text/html, */*")]

    def _call (self, path,params = None ,method = "GET" ,tries =2) :
        url = BASE + path if path.startswith("/") else path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        err = None

        for _ in range( tries ):
            try:
                req = urllib.request.Request(url, method=method,
                                             data=b"" if method =="POST" else None)
                with self.op.open(req,timeout= 45 )as r:#45s, searchResults can hang on fat terms
                    return r.read( ).decode ( "utf-8","replace")
            except ( urllib.error.URLError , urllib.error.HTTPError , OSError,TimeoutError) as e:
                err =e
                time.sleep(1.5)
        raise RuntimeError(f"{path}: {err}")

    def terms(self ):
        raw = self._call("/classSearch/getTerms", {"searchTerm": "", "offset": 1, "max": 99})
        #goes back to ~2016, good enough to spot the yearly-vs-every-term pattern
        return{ t [ "code"]:t.get ("description","" ) for t in json.loads (raw ) if t.get ("code") }

    def new_term(self, code):
        # reset + bind. do not reorder, the reset must land first
        self._call( "/classSearch/resetDataForm" )
        self._call( "/term/search" ,
                   {"term": code, "studyPath": "", "studyPathText": "",
                    "startDatepicker" :"" , "endDatepicker" :""}, method="POST" )

    def sections(self, term, subject, page_max=500):
        # full section list for one subject. do NOT pass txt_courseNumber here:
        #banner answers with totalCount=1 and zero rows, bug or "feature", who knows
        got , page,total = [] , 1, None
        while True:
            raw =self._call ( "/searchResults/searchResults",{
                "txt_term": term, "txt_subject": subject, "txt_courseNumber": "",
                "txt_credithourlow" :"" ,"txt_credithourhigh" : "",
                "startDatepicker" :"" , "endDatepicker":"" ,
                "pageOffset": page, "pageMaxSize": page_max,
                "sortColumns":"subjectDescription" , "sortDirections" : "ascending",
            } )
            body = json.loads(raw)
            if not body.get("success"):

                break
            rows = body.get("data") or []
            total= body.get ( "totalCount",total)
            got.extend( rows )
            # print(term, subject, len(got))  # debug, keep around
            if not rows or len(rows) < page_max or ( total is not None and len ( got)>=total) :
                break
            page += 1
        return got

    @staticmethod
    def pick( s ) :
        # banner sends a pile of fields per row, cache only the ones that matter
        keys= ( "courseReferenceNumber" , "sequenceNumber", "courseNumber" ,"courseTitle" ,
                "creditHours", "scheduleTypeDescription", "instructionalMethodDescription",
                "campusDescription","maximumEnrollment", "enrollment" , "waitCount",
                "waitAvailable")
        return{k :s.get (k)for k in keys if s.get ( k)is not None }

    def fetch_term(self, term, subjects):
        rows={ }
        for subj in subjects :


            # YES this needs to happen before every single subject. banner keeps a
            #materialized result set per sesion and later queries just replay the
            #first subject's rows silently. cost me an afternoon, dont delete
            self._call ("/classSearch/resetDataForm" )
            self._call( "/term/search",
                       {"term": term , "studyPath" : "" ,"studyPathText": "",
                        "startDatepicker": "","endDatepicker":"" },method= "POST")
            secs = self.sections ( term, subj )
            if secs :
                rows [ subj] = [self.pick(x) for x in secs]
            #time.sleep(0.25)  # was 0.25 back when this was single threaded
            time.sleep(0.1)
        return rows

    def course_description (self ,term ,crn ):
        raw= self._call ( "/searchResults/getCourseDescription" ,
                         { "term":term, "courseReferenceNumber":crn })
        link = re.search(r'href="(https?://[^"]*uvic\.ca[^"]*)"', raw)
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^.*?course description[^.]*\.\s*", "", text, flags=re.I)
        # TODO cache this, desc hits banner every single time and banner is slow
        return {"url": link.group(1) if link else None, "text": text[:500]}


#cache = data/offerings.json, one file, {term: {subjects: {CSC: [row,..]}}}

def load_cache(path):
    if os.path.exists ( path ) :
        with open ( path,"r" ,encoding= "utf-8" ) as f:
            try:
                return json.load ( f )
            except json.JSONDecodeError:

                sys.exit(f"not valid json: {path}: delete it and re-run sync")
    return {"fetched_at": None, "term_desc": {}, "terms": {}}


def save_cache (cache,path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cache[ "fetched_at"] =time.strftime ( "%Y-%m-%dT%H:%M")
    with open(path ,"w", encoding = "utf-8" )as f :
        json.dump( cache , f , ensure_ascii=False,separators= (",", ":") )


def live_terms(cache):

    # terms where at least one synced subject actually has rows; banner lists
    #terms that arent published yet and those come back with zero everything
    return sorted(t for t, v in cache["terms"].items() if v["subjects"])


def do_sync(a):
    cache =load_cache (a.cache )
    subjects=[s.strip ( ).upper( ) for s in a.subjects.split (",")if s.strip ( )]
    banner= Banner()
    all_terms=banner.terms( )
    cache.setdefault("term_desc", {}).update(all_terms)

    if a.term:
        wanted = [t.strip() for t in a.term.split(",") if t.strip()]
    else:
        cutoff=f"{date.today().year - a.years}01" if a.years else None
        wanted = [c for c in all_terms
                  if c [ 4: 6]in SEASON_BY_MM and ( cutoff is None or c >= cutoff)]

    wanted.sort()

    todo= [ t for t in wanted if a.refresh or t not in cache ["terms" ]]
    if not todo :
        print( f"cache already covers {len(cache['terms'])} terms "
              f"({', '.join(live_terms(cache)[-3:])})" )
        return

    def work (term):
        b = banner if a.jobs <= 1 else Banner()   #each worker needs its own sesion
        return term, b.fetch_term (term , subjects)

    done =failed =0
    with concurrent.futures.ThreadPoolExecutor( max_workers= max(1,a.jobs)) as pool:


        pending= { pool.submit(work , t) : t for t in todo}

        for fut in concurrent.futures.as_completed ( pending ):
            term = pending [fut]
            try :
                _ ,rows=fut.result()
            except (RuntimeError, KeyError) as e:
                failed +=1


                print(f"  ! {term} failed: {e}",file=sys.stderr )
                continue
            counts = ", ".join(f"{k} {len(v)}" for k, v in sorted(rows.items())) or "(nothing published)"
            cache[ "terms"][ term ] ={"subjects" :rows , "desc": all_terms.get( term, "" ) }
            done += 1
            print( f"  {term}  {counts}" )
            if done % 5 == 0:
                save_cache(cache, a.cache)   # checkpoint mid run, cache is a few MB


    save_cache( cache, a.cache )
    print( f"synced {done}/{len(todo)} terms ({failed} failed) -> {a.cache}" )
    #TODO: retry just the failed terms on a second pass instead of leaving holes


def season( code) :
    return SEASON_BY_MM.get (code [ 4 :6] ,"?" )


def next_term_codes(after_code ,n=3 ) :
    y , mm = int ( after_code[:4] ), after_code[ 4 :6]
    seq = sorted([(y, "01" ) , ( y, "05" ) , ( y, "09"),( y + 1, "01"), (y+ 1 ,"05"), (y +1 ,"09" )])
    out= []
    for yy , m in seq :


        c = f"{yy}{m}"
        if c > after_code:
            out.append(c)
        if len (out )>= n:
            break
    return out


def term_label ( code ) :
    names = {"01": "Spring", "05": "Summer", "09": "Fall"}
    return f"{names.get(code[4:6], '?')} {code[:4]}"


def by_course(cache):
    # (subject, number) -> {title, terms: chronological}
    out = {}
    for term in live_terms( cache ):
        for subj , secs in cache[ "terms" ][term] [ "subjects"].items():
            for s in secs:
                num =str (s.get ( "courseNumber", "" )).zfill( 3 )
                c=out.setdefault(( subj,num ), {"title":s.get ("courseTitle" ) , "terms" : [] })
                c["title"] = c["title"] or s.get("courseTitle")

                if term not in c["terms"] :
                    c["terms" ].append( term)
    return out


def course_stats (subj ,num, byc,active, win ) :
    key = ( subj,num )
    if key not in byc:
        return None
    terms=sorted( byc[key ][ "terms" ])
    # odds the course shows up in a given season, out of the last `win` years
    yrs= {str (y ) for y in range ( date.today ( ).year+1 - win, date.today( ).year+1) }
    seen={ }

    for t in terms:

        if t [ : 4 ]in yrs:
            seen.setdefault ( season(t) , set( )).add( t[: 4 ])
    probs = {s: round(len(seen.get(s, set())) / len(yrs), 2) for s in ("FALL", "SPR", "SUM")}
    last=terms[-1 ]
    gap= [ t for t in active if t>last] #published terms after the last offering
    return{
        "key": key, "title": byc[key]["title"], "terms": terms, "last": last,
        "probs" :probs, "gap_terms" : gap,
        "future_listed" :last>max (active)if active else False,
        # <3 terms means we cant say much about the pattern anyway
        "regular":len(terms)>= 3,
    }


def parse_ids ( tokens ):
    # CSC  CSC 370  csc370  CSC-370  all end up as (CSC, 370)
    joined = " ".join(tokens)
    parts =re.split (r"[,\s]+", joined.upper () )
    specs ,i = [] ,0
    while i < len( parts ) :
        t = parts[i]
        m= re.fullmatch( r"([A-Z]{2,4})(\d{1,3}[A-Z]?)", t )
        if m:
            specs.append((m.group(1), m.group(2).zfill(3)))
            i+=1
            continue
        m =re.fullmatch ( r"([A-Z]{2,4})" ,t )
        if m and i +1< len ( parts) and re.fullmatch( r"\d{1,3}[A-Z]?" ,parts [ i+1 ]) :
            specs.append ( (m.group ( 1) , parts [i +1].zfill( 3)) )
            i += 2

            continue
        i+=1
    return specs


def timeline (terms,codes,mark="#", none = "·" ):
    return "".join ( mark if t in terms else none for t in codes)


def do_check ( a) :
    cache = load_cache(a.cache)
    byc =by_course ( cache )
    active = live_terms ( cache)
    if not active:
        print("cache empty: run: uvic_courses.py sync")
        return
    upcoming=next_term_codes(max( active) ,3)

    specs=parse_ids ( a.course )
    if not specs:
        print ( "could not parse course ids; e.g. check CSC 370 or CSC370" )


        return
    for subj ,num in specs:
        v=course_stats(subj ,num , byc ,active, a.window)
        if v is None :
            print( f"\n{subj} {num}: not found in cache (subject not synced, or never offered in window). "
                  f"try:  list {subj}")
            continue
        probs = v["probs"]
        chips = "  ".join(f"{k} {pct:.0%}" for k, pct in
                          ( ( "Fall", probs[ "FALL"] ) , ( "Spring" ,probs[ "SPR"] ), ("Summer", probs [ "SUM"] ) ))
        print( f"\n{subj} {num}  {v['title'] or ''}")
        print (f"  seen {len(v['terms'])} term(s) · last offered {term_label(v['last'])}")
        if v["future_listed"]:
            print(f"  ALREADY LISTED in the published timetable for {term_label(v['last'])}")
        elif v["gap_terms"]:
            print(f"  !!! expected-but-missing for {len(v['gap_terms'])} straight published term(s)")
        print(f"  seasonal frequency over last {a.window}y:  {chips}")
        print(f"  timeline [{active[0]} … {active[-1]}]:" )
        print (f"              {timeline(set(v['terms']), active)}" )


        print("  forecast:")
        for code in upcoming:
            if code in active :
                verdict ="listed" if code in v["terms"] else "not listed"
                print ( f"    {term_label(code):<16} {verdict}")
            else:
                p = probs[season(code)]
                tag = "LIKELY" if p >=0.8 else( "possible" if p >=0.45 else "unlikely" )
                print(f"    {term_label(code):<16} {tag:<8} {int(round(p * 100)):>3}%  ({SEASON_LABEL[season(code)]})")


def do_list (a) :
    cache= load_cache ( a.cache )

    byc=by_course ( cache )
    subj= a.subject.upper ( )
    active = live_terms(cache)
    rows = [ ]
    for k in byc:
        if k [0 ] !=subj :
            continue
        rows.append(course_stats(subj, k[1], byc, active, a.window))


    def norm_num( k):


        # shots the leading digit, suffix letters break the int() so strip them
        return int ( re.sub(r"\D" , "" , k[ 1 ] )or 0 )
    if a.sort=="code" :

        rows.sort ( key= lambda v:( norm_num( v[ "key"]), v["key"][ 1]))
    else:
        rows.sort(key=lambda v: v["last"], reverse=True)
    if not rows:
        print( f"no {subj} data cached: run sync with that subject")
        return
    print( f"{len(rows)} {subj} courses in cache ({','.join(DEFAULT_SUBJECTS)} default)")
    print(f"{'course':<10}{'title':<42}{'last offered':<16}  F/SP/SU")
    for v in rows:
        p = v ["probs" ]
        freq= f"{p['FALL']:.0%}/{p['SPR']:.0%}/{p['SUM']:.0%}"
        head= v[ "key" ] [ 0]+ " "+v ["key"][ 1]
        flag =""
        if v ["future_listed"] :
            flag = "  (already listed)"
        elif len(v["gap_terms"]) >= 4:
            flag = f"  <-- absent {len(v['gap_terms'])}+ terms"
        print( f"{head:<10}{(v['title'] or '')[:40]:<42}{term_label(v['last']):<16}  {freq}{flag}" )


def do_stale(a):


    cache = load_cache(a.cache)
    byc= by_course (cache )
    active = live_terms(cache)
    subs = [s.strip().upper() for s in a.subjects.split(",") if s.strip()]
    rows = []
    for key in byc :

        if key[ 0] not in subs :
            continue
        v =course_stats (* key ,byc ,active , a.window )
        #regular == we have enough history to call it "was offered on a loop"
        if not v or not v [ "regular" ] :
            continue
        if len(v[ "gap_terms"])>= a.gap:
            rows.append (v )
    if not rows :
        print(f"nothing looks dormant in {','.join(subs)} (gap >= {a.gap} terms)")
        return
    rows.sort(key=lambda v: len(v["gap_terms"]), reverse=True)
    print( f"{len(rows)} courses absent for >= {a.gap} straight published terms:\n" )
    print(f"{'course':<10}{'title':<42}{'last offered':<16}gap" )
    for v in rows:

        head =v ["key" ][ 0] +" "+ v ["key"][1]
        print(f"{head:<10}{(v['title'] or '')[:40]:<42}{term_label(v['last']):<16}{len(v['gap_terms'])} terms")
    #sabbaticals show up here too, so "dormant" is a guess not a verdict. ymmv


def do_desc(a ) :
    cache = load_cache(a.cache )
    active = live_terms ( cache )
    if not active :
        print("cache empty: run sync first")
        return
    subj=a.subject.upper ( )
    num= a.course.zfill(3 )
    banner=Banner ( )
    for term in reversed(active) :
        secs = cache["terms" ][term ] [ "subjects"].get(subj) or[ ]
        base =[ s for s in secs if str (s.get ( "courseNumber" ,"" )).zfill(3 ) ==num ]
        if base :
            s = base[0 ]
            banner.new_term(term)
            d = banner.course_description(term, s["courseReferenceNumber"])

            #print(d)  # debug
            print (f"{subj} {num}: {s.get('courseTitle')}   ({term_label(term)}, CRN {s.get('courseReferenceNumber')})")

            print(d["text"])


            if d["url"]:
                print ( d ["url" ] )
            return
    print (f"{subj} {num}: no cached sections found (run sync?):")


def do_terms (a) :
    cache= load_cache (a.cache )
    for t in sorted( cache[ "terms"]):
        per = cache["terms"][t]["subjects"]
        counts = ", ".join(f"{k} {len(v)}" for k, v in sorted(per.items())) or "-"

        gap = "" if per else "   (no data: not published or empty)"
        print (f"{t}  {term_label(t):<16} {counts}{gap}")


#the html report is one self contained file, css/js inline, DATA as json.
# a note on the js at the bottom: dont delete it, the generated page is a payload
# kept byte-for-byte stable on purpose (see git history for the lightstrip grid fix)

REPORT_CSS="""
:root{--ink:#20242b;--pale:#7a7f88;--bg:#faf9f7;--card:#fff;--line:#e5e1d9;--green:#2e6f4e;--blue:#375a8d;--amber:#b2642a}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 -apple-system,BlinkMacSystemFont,'Helvetica Neue',sans-serif;color:var(--ink);background:var(--bg)}
header{padding:28px 5vw 12px;border-bottom:1px solid var(--line)}
header h1{margin:0 0 4px;font-size:22px}
header p{margin:0;color:var(--pale);font-size:13px;max-width:120ch}
.controls{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;gap:10px;flex-wrap:wrap;padding:12px 5vw;border-bottom:1px solid var(--line)}
.controls input,.controls select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:#fff;font-size:14px}
.controls input{min-width:260px;flex:1;max-width:420px}
label.tog{display:flex;gap:6px;align-items:center;color:var(--pale);font-size:13px;padding-top:7px}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px;padding:18px 5vw 70px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}
.card.stale{border-color:#d8b28a}
.head{display:flex;align-items:baseline;gap:10px}
.code{font-weight:700;font-size:15px}
.title{color:var(--pale);font-size:13px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.warn{font-size:11px;color:var(--amber);border:1px solid #e2b98c;border-radius:6px;padding:1px 7px;white-space:nowrap}
.row1{display:flex;gap:6px;align-items:center;margin:10px 0 8px;flex-wrap:wrap}
.chip{font-size:11px;border-radius:6px;padding:2px 8px}
.chip.Fall{background:#e7f0eb;color:#2e6f4e}
.chip.Spring{background:#eaeef5;color:#375a8d}
.chip.Summer{background:#f6efe2;color:#8a6420}
.last{margin-left:auto;color:var(--pale);font-size:12px;white-space:nowrap}
.strip{display:grid;gap:2px}
.strip .c{width:auto;height:13px;border-radius:2px;display:block}
.fcrow{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;font-size:11px}
.fc{border-radius:6px;padding:2px 8px;border:1px solid var(--line);color:var(--pale)}
.fc.likely{border-color:var(--green);color:var(--green);background:#f4f8f5}
.fc.maybe{border-color:#c9b26a;color:#7c6a24}
.fc.hit{border-color:var(--blue);color:var(--blue);background:#f0f4fa}
.fc.miss{color:#9aa0a8}
.empty{color:var(--pale);padding:40px 0}
"""

REPORT_JS = """
const D=DATA, EL=i=>document.getElementById(i);
const esc=s=>String(s).replace(/[&<>']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;'}[m]));
function render(){
  const q=EL('q').value.toLowerCase(), sub=EL('subject').value, lvl=EL('level').value,
        hide=EL('hidestale').checked;
  let htmlout='', n=0;
  for (const k of Object.keys(D.courses)){
    const c=D.courses[k];
    if(sub && c.subject!==sub) continue;
    if(lvl && (lvl==='5+'? Number(c.num)<500 : String(c.num).charAt(0)!==lvl)) continue;
    if(hide && c.gapN>=4 && c.regular) continue;
    if(q && !((c.subject+c.num+' '+(c.title||'')).toLowerCase().includes(q))) continue;
    n++;
    const lights=D.active.map(t=>`<span class="c" title="${D.termLabel[t]}${c.terms.includes(t)?' - offered':''}" style="background:${c.terms.includes(t)?'#2e6f4e':'#e6e2da'}"></span>`).join('');
    const strip=`<div class="strip" style="grid-template-columns:repeat(${D.active.length},1fr)">${lights}</div>`;
    const probs=['FALL','SPR','SUM'].map(s=>`<span class="chip ${D.chip[s]}">${D.chip[s]} ${Math.round(c.probs[s]*100)}%</span>`).join('');
    let fc='';
    for (const u of D.upcoming){
      if (D.active.includes(u)){
        const hit=c.terms.includes(u);
        fc+=`<span class="fc ${hit?'hit':'miss'}">${D.termLabel[u]}: ${hit?'listed':'not listed'}</span>`;
      } else {
        const p=c.probs[D.seasonOf[u]], cls=p>=0.8?'likely':(p>=0.45?'maybe':'');
        fc+=`<span class="fc ${cls}">${D.termLabel[u]}: ${Math.round(p*100)}%</span>`;
      }
    }
    const warn=(c.gapN>=4 && c.regular)?`<span class="warn">absent ${c.gapN} terms</span>`:'';
    htmlout+=`<div class="card ${warn?'stale':''}">
      <div class="head"><span class="code">${esc(c.subject)} ${esc(c.num)}</span>
      <span class="title">${esc(c.title||'')}</span>${warn}</div>
      <div class="row1">${probs}<span class="last">last: ${D.termLabel[c.last]}</span></div>
      ${strip}
      <div class="fcrow">${fc}</div></div>`;
  }
  EL('count').textContent=n+' courses';
  EL('grid').innerHTML=htmlout||'<p class="empty">no matches</p>';
}
EL('q').addEventListener('input',render);
['subject','level','hidestale'].forEach(i=>EL(i).addEventListener('change',render));
render();
"""


def do_report (a ) :
    cache= load_cache (a.cache)
    byc = by_course( cache)
    active = live_terms(cache)
    if not active:
        print ( "cache empty: run sync first" )
        return
    upcoming=next_term_codes( max(active ), 3 )

    courses= { }

    for key in byc:
        subj, num =key
        v =course_stats ( subj,num, byc, active , a.window)
        courses[f"{subj} {num}" ]= {
            "subject" : subj, "num" : num ,"title" : v["title"] , "terms":v[ "terms"],
            "last": v[ "last" ],"probs" : v [ "probs" ] ,"gapN": len(v["gap_terms" ]),
            "regular": v["regular"],
        }
    data ={
        "active": active,
        "upcoming":upcoming ,
        "seasonOf":{t:season (t )for t in active+ upcoming} ,
        "termLabel" :{ t :term_label(t)for t in active+upcoming } ,
        "chip": SEASON_CHIP,
        "courses": courses,
    }
    subs = sorted({s for s, _ in byc})
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UVic engineering &amp; CS: offering history &amp; forecast</title>
<style>{REPORT_CSS}</style></head><body>
<header>
<h1>UVic offering history &amp; upcoming-term forecast</h1>
<p>{htmlesc.escape(' · '.join(subs))} · {len(courses)} courses · published terms {active[0]}–{active[-1]} ·
synced {htmlesc.escape(cache.get('fetched_at') or '')} · source: banner.uvic.ca (public timetable search)</p>
<p>Colored blocks = course offered that term. Season % = share of the last {a.window} years the course ran in
Fall / Spring / Summer. Forecast chips are statistical inferences from history (not official UVic
schedules); courses marked "listed" already appear in the next published timetable.</p>
</header>
<div class="controls">
  <input id="q" type="search" placeholder="search subject, number, or title …">
  <select id="subject"><option value="">all subjects</option>
    {''.join(f'<option>{s}</option>' for s in subs)}</select>
  <select id="level"><option value="">all levels</option>
    {''.join(f'<option>{i}</option>' for i in ('1', '2', '3', '4'))}
    <option value="5+">grad (500+)</option></select>
  <label class="tog"><input type="checkbox" id="hidestale"> hide dormant</label>
  <span style="color:var(--pale);font-size:12px;padding-top:7px" id="count"></span>
</div>
<div id="grid"></div>
<script>const DATA = {json.dumps(data, ensure_ascii=False)};</script>
<script>{REPORT_JS}</script>
</body></html>"""
    os.makedirs ( os.path.dirname (a.out),exist_ok=True)
    with open (a.out ,"w" ,encoding= "utf-8" ) as f :
        f.write (html)
    print (f"report -> {a.out}  ({len(courses)} courses, {len(active)} terms, forecast to {max(upcoming)})" )
    #FIXME levels treat 349A as first-year because of the leading 3; whatever, close enough
    #FIXME the season % window is years not terms, fine for now


def main( argv ) :
    p = argparse.ArgumentParser(
        prog = "uvic_courses" ,
        description = "UVic course offering history & next-term forecast, from banner.uvic.ca")

    p.add_argument( "--cache", default=DEFAULT_CACHE )
    sub = p.add_subparsers ( dest ="cmd", required =True)

    s=sub.add_parser ("sync")
    s.add_argument("--subjects" , default=",".join(DEFAULT_SUBJECTS ))
    s.add_argument("--years", type=int, default=10)
    s.add_argument("--term", help="comma-separated term codes override (e.g. 202609,202701)")
    s.add_argument ( "--refresh" , action= "store_true")
    s.add_argument("--jobs", type =int ,default =3)

    c =sub.add_parser ("check")

    c.add_argument("course", nargs="+")
    c.add_argument( "--window",type= int , default=8 )

    l = sub.add_parser("list")

    l.add_argument( "subject" )
    l.add_argument ( "--sort" , choices= ["code", "last" ] , default= "code")
    l.add_argument("--window", type =int , default =8 )

    st=sub.add_parser("stale")
    st.add_argument( "--subjects" , default= ",".join(DEFAULT_SUBJECTS ))
    st.add_argument("--gap" ,type= int, default = 4)
    st.add_argument ("--window",type=int , default = 8)

    d =sub.add_parser("desc")
    d.add_argument("subject")

    d.add_argument("course")

    sub.add_parser("terms")

    r =sub.add_parser("report")
    r.add_argument ("--out" ,default =DEFAULT_REPORT)
    r.add_argument ("--window" , type = int ,default= 8 )

    a = p.parse_args(argv)
    dispatch = {"sync": do_sync, "check": do_check, "list": do_list,
                "stale" : do_stale , "desc":do_desc, "terms" :do_terms,"report":do_report }
    dispatch[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv [ 1 : ] )
