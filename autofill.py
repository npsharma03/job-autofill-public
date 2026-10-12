#!/usr/bin/env python3
"""
autofill.py - fills out job applications for you. YOU click Submit.

Usage:
    python autofill.py                      # runs through every URL in jobs.txt
    python autofill.py --url <job-url>      # just one job
    python autofill.py --overwrite          # overwrite fields the site pre-filled
    python autofill.py --redo               # re-do jobs already logged as submitted

Flow per job:
    1. opens the job page (clicks "Apply" if needed)
    2. fills everything it recognises (green outline = filled by me)
    3. outlines required fields it couldn't fill in RED
    4. waits for you: review, fix, click Submit yourself, then tell the terminal

Works best on plain-form ATSs (Greenhouse, Lever, Ashby, Workable, SmartRecruiters).
Multi-step portals (Workday etc.) work page-by-page: press [r] after each "Next".
"""
import argparse
import csv
import difflib
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent

# --------------------------------------------------------------------------- #
# JS injected into pages
# --------------------------------------------------------------------------- #
COLLECT_JS = r"""
() => {
  const txt = e => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const out = [];
  let n = 0;
  document.querySelectorAll('input, textarea, select').forEach(el => {
    const type = (el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) return;
    if (el.disabled) return;
    const special = ['file', 'radio', 'checkbox'].includes(type);
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = el.getBoundingClientRect();
    if (!special && (r.width === 0 || r.height === 0)) return;
    if (!special && el.getAttribute('aria-hidden') === 'true') return;

    let own = '';
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) own = txt(l);
    }
    if (!own) { const w = el.closest('label'); if (w) own = txt(w); }
    const lb = el.getAttribute('aria-labelledby');
    if (!own && lb) own = lb.split(/\s+/).map(i => txt(document.getElementById(i))).join(' ').trim();
    if (!own && type === 'radio' && el.nextSibling) own = (el.nextSibling.textContent || '').trim();

    let question = own;
    if (type === 'radio' || type === 'checkbox') {
      question = '';
      const fs = el.closest('fieldset');
      const lg = fs && fs.querySelector('legend');
      if (lg) question = txt(lg);
      if (!question) {
        const g = el.closest('[role="radiogroup"],[role="group"]');
        if (g) question = g.getAttribute('aria-label') ||
          txt(document.getElementById(g.getAttribute('aria-labelledby') || '')) || '';
      }
      if (!question) {
        let p = el.parentElement;
        for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
          const t = txt(p);
          if (t.length > own.length + 8 && t.length < 400) { question = t; break; }
        }
      }
    }

    const id = 'aa' + (n++);
    el.setAttribute('data-aa-id', id);
    out.push({
      aid: id,
      tag: el.tagName.toLowerCase(),
      type: type,
      combobox: el.getAttribute('role') === 'combobox' ||
                el.getAttribute('aria-haspopup') === 'listbox' ||
                !!el.getAttribute('aria-autocomplete'),
      name: el.getAttribute('name') || '',
      id_attr: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      own: own,
      question: question,
      options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()) : [],
      value: (type === 'radio' || type === 'checkbox') ? el.checked
             : (type === 'file' ? el.files.length > 0 : (el.value || '')),
    });
  });
  return out;
}
"""

FLAG_JS = r"""
() => {
  const bad = [];
  const seen = new Set();
  const label = el => {
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    return (t || el.getAttribute('placeholder') || el.name || el.id || 'field').replace(/\s+/g, ' ').trim().slice(0, 80);
  };
  document.querySelectorAll('input, textarea, select').forEach(el => {
    const type = (el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button'].includes(type)) return;
    if (!(el.required || el.getAttribute('aria-required') === 'true')) return;
    if (getComputedStyle(el).display === 'none') return;
    if (!['file', 'radio', 'checkbox'].includes(type) && el.getAttribute('aria-hidden') === 'true') return;
    let empty;
    if (type === 'radio') {
      if (seen.has(el.name)) return; seen.add(el.name);
      empty = !document.querySelector('input[type=radio][name="' + el.name + '"]:checked');
    } else if (type === 'checkbox') empty = !el.checked;
    else if (type === 'file') empty = el.files.length === 0;
    else empty = !(el.value || '').trim();
    if (empty) { el.style.outline = '2px solid #ef4444'; bad.push(label(el)); }
  });
  return bad;
}
"""

# --------------------------------------------------------------------------- #
# Matching rules  (order matters: first match wins)
# --------------------------------------------------------------------------- #
TEXT_RULES = [
    (r"preferred (first )?name", "preferred_name"),
    (r"first name|given name|\bfname\b", "first_name"),
    (r"last name|family name|surname|\blname\b", "last_name"),
    (r"e ?mail", "email"),
    (r"phone|mobile|telephone", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"portfolio|personal (web)?site|\bwebsite\b|other url", "website"),
    (r"current (company|employer)|\bemployer\b|\bcompany\b|organi[sz]ation", "current_company"),
    (r"current (job )?title|current role|job title", "current_title"),
    (r"street|address line|^address", "address"),
    (r"location|where .*(based|located)|current city", "location"),
    (r"\bcity\b", "city"),
    (r"\bstate\b|province", "state"),
    (r"\bzip\b|postal", "zip"),
    (r"\bcountry\b", "country"),
    (r"salary|compensation|pay expect|expected pay", "expected_salary"),
    (r"notice period|start date|available to start|earliest.*start|when can you start", "start_date"),
    (r"years of .*experience|years.*experience|experience.*years", "years_experience"),
    (r"how did you (hear|find)|where did you (hear|find)|referral source", "how_heard"),
    (r"pronoun", "pronouns"),
    (r"cover letter|additional information|anything else", "cover_letter"),
]

CHOICE_RULES = [
    (r"(authori[sz]ed|eligible|legally|right|permitted).{0,40}work", "work_authorized"),
    (r"sponsor|visa", "needs_sponsorship"),
    (r"relocat", "willing_to_relocate"),
    (r"at least 18|over 18|18 years", "over_18"),
    (r"(previously|ever|formerly).{0,30}(worked|employed)", "previously_employed"),
    (r"background check", "background_check"),
    (r"on site|onsite|hybrid|in office|commut", "onsite_ok"),
    (r"hispanic|latino", "hispanic"),
    (r"gender", "gender"),
    (r"\brace\b|ethnic", "ethnicity"),
    (r"veteran", "veteran"),
    (r"disabilit", "disability"),
    (r"lgbt|sexual orientation", "lgbtq"),
]

PLACEHOLDER_OPT = re.compile(r"^\s*(select|choose|please|pick|--|—|\.\.\.)", re.I)
DECLINE_OPT = re.compile(
    r"decline|prefer not|don.?t wish|do not wish|not (wish )?to (say|disclose|answer|identify)|rather not", re.I)
APPLY_BTN = re.compile(r"^\s*apply( now| for this job| to this job| for this position)?\s*$", re.I)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def norm(s):
    s = re.sub(r"[_\-\[\]\.\*:()]", " ", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def label_of(f):
    t = f["question"] or f["own"] or f["aria"] or f["placeholder"] or f["name"] or f["id_attr"] or "field"
    return re.sub(r"\s+", " ", t).strip()[:70]


def blob_of(f):
    return norm(" ".join([f["question"], f["own"], f["aria"], f["placeholder"],
                          f["name"], f["id_attr"], f["autocomplete"]]))


def best_option(options, desired):
    """Pick the option text that best matches `desired`. Returns the option or None."""
    opts = [o for o in options if o and not PLACEHOLDER_OPT.match(o)]
    d = (desired or "").strip().lower()
    if not opts or not d:
        return None
    if DECLINE_OPT.search(d):
        for o in opts:
            if DECLINE_OPT.search(o):
                return o
    for o in opts:
        if o.lower() == d:
            return o
    if d in ("yes", "no"):
        for o in opts:
            if re.match(rf"{d}\b", o.lower()):
                return o
        return None
    for o in opts:
        ol = o.lower()
        if d in ol or (len(ol) > 3 and ol in d):
            return o
    m = difflib.get_close_matches(d, [o.lower() for o in opts], n=1, cutoff=0.6)
    if m:
        return next(o for o in opts if o.lower() == m[0])
    return None


def load_profile(path):
    with open(path, "r", encoding="utf-8") as fh:
        P = yaml.safe_load(fh)
    flat = {}
    for section in ("personal", "links", "work"):
        flat.update({k: ("" if v is None else str(v)) for k, v in (P.get(section) or {}).items()})
    flat["full_name"] = f"{flat.get('first_name', '')} {flat.get('last_name', '')}".strip()
    answers = {k: str(v) for k, v in (P.get("answers") or {}).items() if v not in (None, "")}
    files = P.get("files") or {}
    return flat, answers, files


def resolve_path(p):
    if not p:
        return None
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = HERE / path
    return path if path.exists() else None


def guess_company_role(url, title):
    company, role = "", ""
    m = re.match(r"(?:job application for\s+)?(.+?)\s+(?:at|@)\s+(.+?)(?:\s+[-|–].*)?$", title or "", re.I)
    if m:
        role, company = m.group(1).strip(), m.group(2).strip()
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if not company and parts and any(h in host for h in
                                     ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com")):
        company = parts[0].replace("-", " ").title()
    if not company and host.endswith("myworkdayjobs.com"):
        company = host.split(".")[0].title()
    if not role and title:
        chunks = [c.strip() for c in re.split(r"\s+(?:at|[-–|@])\s+", title) if c.strip()]
        others = [c for c in chunks if c.lower() != company.lower()]
        role = (others or chunks or [""])[0]
    return company or "your company", role or "this role"


def render_cover_letter(flat, company, role):
    path = HERE / "cover_letter.txt"
    if not path.exists():
        return ""
    data = SafeDict(flat)
    data.update(company=company, role=role)
    return path.read_text(encoding="utf-8").format_map(data).strip()


def resolve_text(f, primary, blob, flat):
    if re.fullmatch(r"(full |legal |your )?name", primary):
        return flat.get("full_name")
    if len(primary) > 70:  # it's a question, not a simple field
        return None
    for rx, key in TEXT_RULES:
        if key == "cover_letter" and f["tag"] != "textarea":
            continue
        if f["tag"] == "textarea" and key != "cover_letter":
            continue
        if re.search(rx, blob):
            val = flat.get(key)
            if val:
                return val
            return None
    return None


def resolve_choice(blob, answers):
    for rx, key in CHOICE_RULES:
        if re.search(rx, blob) and key in answers:
            return answers[key]
    return None


# --------------------------------------------------------------------------- #
# Filling
# --------------------------------------------------------------------------- #
def fill_combobox(frame, loc, desired):
    page = frame.page
    loc.click()
    page.wait_for_timeout(250)
    if not DECLINE_OPT.search(desired):
        loc.type(desired[:25], delay=30)
        page.wait_for_timeout(700)
    opts = frame.locator('[role="option"]:visible')
    texts = [t.strip() for t in opts.all_inner_texts()]
    if texts:
        pick = best_option(texts, desired) or (texts[0] if not DECLINE_OPT.search(desired) else None)
        if pick:
            opts.nth(texts.index(pick)).click()
            return True
        page.keyboard.press("Escape")
        return False
    page.keyboard.press("Enter")
    return True


def fill_field(frame, f, flat, answers, files, overwrite, cover_text):
    """Returns (state, label, shown_value). state: filled | skipped | todo"""
    t, label, blob = f["type"], label_of(f), blob_of(f)
    primary = norm(f["own"] or f["aria"] or f["placeholder"])
    loc = frame.locator(f'[data-aa-id="{f["aid"]}"]').first

    # ---- file uploads
    if t == "file":
        if f["value"] and not overwrite:
            return "skipped", label, ""
        if re.search(r"cover", blob):
            path = resolve_path(files.get("cover_letter_pdf"))
        elif re.search(r"resume|\bcv\b|curriculum", blob):
            path = resolve_path(files.get("resume"))
        else:
            path = None
        if not path:
            return "todo", label, ""
        loc.set_input_files(str(path))
        return "filled", label, path.name

    if f["value"] and not overwrite:
        return "skipped", label, ""

    is_choice = f["tag"] == "select" or f["combobox"]
    if is_choice:
        want = resolve_choice(blob, answers) or resolve_text(f, primary, blob, flat)
    else:
        want = resolve_text(f, primary, blob, flat) or resolve_choice(blob, answers)
    if f["tag"] == "textarea" and re.search(r"cover letter|additional information|anything else", blob) and cover_text:
        want = cover_text
    if not want:
        return "todo", label, ""

    if t == "number":
        want = re.sub(r"[^\d.]", "", want)
    if t == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", want):
        return "todo", label, ""

    if f["tag"] == "select":
        pick = best_option(f["options"], want)
        if not pick:
            return "todo", label, ""
        loc.select_option(index=f["options"].index(pick))
    elif f["combobox"]:
        if not fill_combobox(frame, loc, want):
            return "todo", label, ""
    else:
        loc.fill(want)

    try:
        loc.evaluate("e => { e.style.outline = '2px solid #22c55e'; }")
    except Exception:
        pass
    return "filled", label, (want if len(want) < 40 else want[:37] + "...")


def fill_radios(frame, groups, answers, overwrite):
    results = []
    for _, grp in groups.items():
        if any(g["value"] for g in grp) and not overwrite:
            continue
        question = re.sub(r"\s+", " ", grp[0]["question"]).strip()[:70] or "choice"
        want = resolve_choice(blob_of(grp[0]), answers)
        if not want:
            results.append(("todo", question, ""))
            continue
        labels = [g["own"] for g in grp]
        pick = best_option(labels, want)
        if not pick:
            results.append(("todo", question, ""))
            continue
        loc = frame.locator(f'[data-aa-id="{grp[labels.index(pick)]["aid"]}"]').first
        try:
            loc.check(force=True, timeout=1500)
        except Exception:
            loc.evaluate("e => e.click()")
        results.append(("filled", question, pick))
    return results


def fill_page(page, flat, answers, files, overwrite, company, role):
    cover_text = render_cover_letter(flat, company, role)
    filled, todo = [], []
    for frame in page.frames:
        if frame.url in ("", "about:blank") or "recaptcha" in frame.url:
            continue
        try:
            fields = frame.evaluate(COLLECT_JS)
        except Exception:
            continue
        radios = defaultdict(list)
        for f in fields:
            if f["type"] == "radio":
                radios[f["name"] or f["question"][:80]].append(f)
        for f in fields:
            if f["type"] in ("radio", "checkbox"):
                continue
            try:
                res = fill_field(frame, f, flat, answers, files, overwrite, cover_text)
            except Exception as e:
                res = ("todo", f"{label_of(f)} (error: {str(e)[:40]})", "")
            if res[0] == "filled":
                filled.append(res)
            elif res[0] == "todo":
                todo.append(res)
        for res in fill_radios(frame, radios, answers, overwrite):
            (filled if res[0] == "filled" else todo).append(res)

    flagged = []
    for frame in page.frames:
        try:
            flagged += frame.evaluate(FLAG_JS)
        except Exception:
            pass

    print(f"\n  Filled {len(filled)} field(s):")
    for _, label, val in filled:
        print(f"    ✓ {label}" + (f"  ←  {val}" if val else ""))
    if todo:
        print(f"  Couldn't answer {len(todo)} (check these yourself):")
        for _, label, _ in todo:
            print(f"    ? {label}")
    if flagged:
        print(f"  Required but still empty (outlined RED in the browser):")
        for label in flagged:
            print(f"    ✗ {label}")
    print()


# --------------------------------------------------------------------------- #
# Navigation / logging
# --------------------------------------------------------------------------- #
def maybe_click_apply(ctx, page):
    try:
        if page.locator("input:visible, textarea:visible").count() >= 4:
            return page
        btn = page.get_by_role("link", name=APPLY_BTN).or_(page.get_by_role("button", name=APPLY_BTN)).first
        if btn.count() == 0:
            return page
        before = len(ctx.pages)
        btn.click(timeout=3000)
        page.wait_for_timeout(2000)
        if len(ctx.pages) > before:
            page = ctx.pages[-1]
            page.wait_for_load_state("domcontentloaded")
        return page
    except Exception:
        return page


def read_jobs(path):
    jobs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        jobs.append((parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    return jobs


LOG = HERE / "applications.csv"


def load_submitted():
    if not LOG.exists():
        return set()
    with open(LOG, newline="", encoding="utf-8") as fh:
        return {r["url"] for r in csv.DictReader(fh) if r["status"] == "submitted"}


def log(url, company, role, status):
    new = not LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp", "url", "company", "role", "status"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), url, company, role, status])


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="single job URL")
    ap.add_argument("--jobs", default=str(HERE / "jobs.txt"))
    ap.add_argument("--profile", default=str(HERE / "profile.yaml"))
    ap.add_argument("--overwrite", action="store_true", help="overwrite pre-filled fields")
    ap.add_argument("--redo", action="store_true", help="include jobs already marked submitted")
    args = ap.parse_args()

    if not Path(args.profile).exists():
        sys.exit(f"Missing {args.profile}. Copy profile.example.yaml to profile.yaml and fill it in.")
    flat, answers, files = load_profile(args.profile)

    jobs = [(args.url, "", "")] if args.url else read_jobs(args.jobs)
    submitted = set() if args.redo else load_submitted()

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(HERE / ".browser-profile"), headless=False, viewport=None, args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for i, (url, company, role) in enumerate(jobs, 1):
            if url in submitted:
                print(f"[{i}/{len(jobs)}] already submitted, skipping: {url}")
                continue
            print(f"\n[{i}/{len(jobs)}] {url}")

            for extra in ctx.pages[1:]:
                extra.close()
            page = ctx.pages[0]
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("input, textarea", timeout=10000)
            except Exception:
                pass
            page = maybe_click_apply(ctx, page)
            page.wait_for_timeout(1500)

            g_company, g_role = guess_company_role(url, page.title())
            company, role = company or g_company, role or g_role
            print(f"  → {role} @ {company}")
            fill_page(page, flat, answers, files, args.overwrite, company, role)

            while True:
                choice = input("[Enter] I submitted it   [r] refill this page   [s] skip   [q] quit  > ").strip().lower()
                if choice == "r":
                    page = ctx.pages[-1]
                    page.wait_for_timeout(800)
                    fill_page(page, flat, answers, files, args.overwrite, company, role)
                    continue
                if choice == "s":
                    log(url, company, role, "skipped")
                elif choice == "q":
                    log(url, company, role, "quit")
                    ctx.close()
                    return
                else:
                    log(url, company, role, "submitted")
                break

        print("\nAll done. Log saved to applications.csv")
        ctx.close()


if __name__ == "__main__":
    main()
