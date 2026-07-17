# NJ School Student-Support Staff Finder

A local Streamlit application that accepts New Jersey school names, identifies the corresponding NJ school and district, discovers and validates the official school or district website, and searches that official domain for publicly displayed professional contact information for student-support staff.

The application is designed around a strict evidence rule: **it never predicts, completes, or generates an email address**. Every reported address must be fully visible on a confirmed official school/district webpage, in a public `mailto:` link, in public structured page data, or in a text-based PDF. Each result retains its source page and evidence snippet.

## What it finds

The automatically searched role categories cover:

- Licensed or clinical mental-health professionals, including therapists, clinicians, LCSWs, LSWs, LPCs, and LACs
- School psychology
- School social work
- Student assistance, substance-use prevention, and crisis support
- School counseling, kept separate from clinical mental-health professionals unless a clinical title or license is displayed
- Mental-health and student-services leadership with clearly relevant responsibilities
- Case managers, student-support specialists, behavior specialists, wellness coordinators, and similar ambiguous titles under **Possible Contacts Requiring Review**

College-only, career-only, admissions, financial-aid, camp, and legal counselors are excluded, along with teachers, nurses, coaches, secretaries, registrars, unrelated administrators, and outside vendors.

Named staff and general department mailboxes are kept separate. Every named record stores the exact displayed role, professional category, displayed credentials or licenses, email, school, district, source URL, and evidence snippet.

## Responsible-use limitations

This tool searches only publicly accessible official New Jersey school and district websites. It does not use data brokers, people-search sites, social media, private directories, or login-protected systems. It does not bypass CAPTCHAs, bot protection, authentication, paywalls, or access restrictions. It respects `robots.txt`, uses conservative request limits, and searches one confirmed official domain at a time.

Use the exported professional contact information responsibly and in accordance with applicable policies and law. A public address is evidence of publication, not permission for unsolicited bulk messaging.

## Requirements

- Python 3.11 or newer
- Internet access while searching schools
- A current Chromium runtime only if JavaScript fallback is enabled

The normal crawler uses `httpx` and Beautiful Soup first. Playwright is invoked only for a likely relevant page whose initial HTML contains too little usable content.

## Installation

Open a terminal in this project folder and create a virtual environment:

```text
python -m venv .venv
```

### Windows

```text
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

If `playwright` or `streamlit` is not found on `PATH`, use the module form:

```text
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m streamlit run app.py
```

### macOS and Linux

```text
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

If the short commands are not found:

```text
.venv/bin/python -m playwright install chromium
.venv/bin/python -m streamlit run app.py
```

Streamlit prints the local browser address, normally `http://localhost:8501`.

## Easiest way to start it

After installation, use either one-click option:

- Open `run.py` in your editor and press its normal **Run** button.
- On Windows, double-click `Run School Finder.bat` in File Explorer.

The launcher automatically uses this project's `.venv`, starts Streamlit correctly, displays one local link, and opens the browser. Keep its terminal window open while using the application; press `Ctrl+C` to stop it.

## Free public hosting with Streamlit Community Cloud

This repository includes the Python and Linux dependency files needed by Streamlit Community Cloud, including a system Chromium browser for the optional JavaScript fallback.

1. Create a GitHub repository and upload this project.
2. Sign in at `https://share.streamlit.io` with GitHub.
3. Choose **Create app**, select the repository and its `main` branch, and set the entrypoint to `app.py`.
4. Choose Python 3.12 and deploy. Streamlit will provide a public `*.streamlit.app` link.

No secrets or API keys are required. Do not upload `.env`, `.venv`, `cache/`, or locally saved search-result files; the included `.gitignore` excludes them.

Community Cloud's filesystem belongs to the running app instance. Treat downloaded page caches and `data/saved_school_results/` as temporary: they can disappear after a reboot or redeployment. The bundled NJDOE school catalog remains available because it is committed with the application. A persistent shared result history would require an external database or a host with persistent storage.

## Using the application

1. Paste one school name per line. New Jersey is assumed automatically; do not add a state.
2. Select **Find Public Staff Emails**. There are no search settings to configure; the app checks every supported role and source type automatically.
3. Watch the per-school status, website-discovery status, pages searched, and overall progress. New contacts appear immediately in **Contacts found so far** while the crawl continues.
4. To add another school while a search is running, add its name on a new line and select the search button again. Existing searches and completed results continue unchanged; only new names are added to the queue.
5. Review the completed results in the separate card for each school.
5. Use the **Source** button beside a contact to inspect the exact evidence page.

Blank lines and repeated names are removed while the first-entered order is preserved. Up to three schools are searched in parallel, while completed results remain displayed in the original input order. Larger batches continue in groups of three. Schools added during a running search use the same queue and do not cancel or restart active work.

Completed results are held in Streamlit session state. Confirming an ambiguous school, providing a missing website, changing a widget, or retrying one school does not discard other completed schools.

Completed and partial school searches are also saved locally in `data/saved_school_results/`. When the same school is requested in a later session, the app shows the original checked date and asks whether to display the saved evidence immediately or search the official website again. Failed, unresolved, and ambiguous attempts are not offered as saved results. Choosing a fresh search replaces the saved record after that search completes.

## School matching

The reusable `NJSchoolDirectory` provider uses this order:

1. A CSV/XLSX specified by `NJ_SCHOOL_DIRECTORY_FILE`
2. The bundled complete NJDOE 2024–2025 school catalog
3. The cached `cache/nj_school_directory.csv`
4. The current public NJDOE School Performance Reports JSON catalog
5. Exact official NJDOE report lookup for a school missing from the catalog
6. The supplied New Jersey high-school list as a supplemental fallback

The NJDOE Homeroom directory remains a canonical reference, but it can reject automated retrieval. The app therefore uses NJDOE's current public School Performance Reports catalog, which supplies school, district, county, municipality, and official website fields. The downloaded directory is cached locally so a later run does not repeatedly fetch it.

Matching expands careful abbreviations such as `HS`, `MS`, and `ES`, normalizes punctuation and hyphens, and preserves meaningful directional words. Exact or uniquely strong matches can continue automatically. Close or weak matches are never silently chosen.

If several NJ schools are plausible, the same page displays a selector in this form:

```text
School — District — Municipality — County
```

Select the right record and choose **Confirm School**. No school outside New Jersey is introduced by the matching provider.

### Supplying a local NJDOE-derived directory

Set `NJ_SCHOOL_DIRECTORY_FILE` in the environment, or copy `.env.example` values into your preferred environment manager. CSV and XLSX files are supported. Column names are matched flexibly, but the file must include:

- School Name
- District Name

Useful optional columns are County, Municipality/City, School URL, District URL, and Source URL.

## Website discovery and unresolved schools

Official URLs present in an NJDOE-derived record are checked first. The app then uses no-key public web search queries combining the exact school, district, municipality, New Jersey, and relevant staff/departments. Third-party directories, social media, news, real-estate sites, and people-search domains are rejected.

A candidate is scored using school-name, district-name, New Jersey/location, page content, and NJDOE-source evidence. Only **Verified** and strongly supported **Likely** websites are crawled automatically.

If a candidate is inconclusive, it is displayed as **Website needs confirmation** and is not crawled until confirmed. If no credible candidate is found, the card and the **Schools Needing Website Help** summary show:

- School, district, municipality, and county
- Discovery attempts made
- Candidate sites considered
- An **Official school or district website** field
- **Use This Website and Search Again**
- **Retry Automatic Website Search**

A manually supplied URL is normalized and its public home page is compared with the selected school/district. A strongly matching site continues immediately. An inconclusive site produces a warning and requires explicit confirmation. A supplied district website is valid; the crawler prioritizes paths for the selected school and student-support departments.

## Crawl and verification behavior

The following responsible limits are fixed internally so the user does not need to configure crawler settings:

- No fixed page-count ceiling; the focused crawl continues until its candidate queue is exhausted
- Crawl depth up to 4
- Up to three schools searched concurrently
- Up to two pages fetched concurrently per domain
- At least 500 ms between requests to the same domain
- 15-second request timeout
- Temporary-network-error retries with exponential backoff
- Same registrable-domain restriction
- `robots.txt` checks

The crawler prioritizes counseling, guidance, psychology, social work, student assistance, mental health, wellness, Child Study Team, pupil services, staff, directory, faculty, department, and contact paths across the requested school's official district domain. Relevant professionals from other schools in that district are included and labeled with their actual school when the official source identifies it. A general staff directory is opened as an entry point, but the crawler follows only profiles whose displayed title or surrounding text matches a supported role; it does not walk every teacher or employee profile. Broad navigation containers also cannot make all nearby links appear relevant. It searches up to three schools concurrently and fetches up to two pages per domain while spacing request starts by at least 500 ms. Schools sharing one district domain also share that domain limit. Unrelated calendars, athletics, assignments, menus, news archives, and PDFs are excluded from the crawl frontier unless their URL or link label contains relevant student-support terms. It also checks focused sitemap links and relevant public PDFs. It does not submit login forms or attempt to circumvent blocking.

For each accepted contact, the internal record includes:

- Exact public email
- Staff name and role when clearly associated
- Role category and contact type
- Exact source URL and page title
- Nearby evidence snippet
- Extraction method
- Checked date/time
- Email, school-match, and website confidence

Duplicate emails are merged. The strongest specific role is retained and all source URLs remain in the record. Staff are sorted by role category, last name, and first name.

If a public PDF has no extractable text, the app records **Scanned PDF found — manual review may be required** and links the PDF. It does not perform large-scale OCR.

## No-result and failure behavior

When an official website was searched but no qualifying public email was found, the school card reports that outcome and shows pages searched, relevant pages, PDFs inspected, JavaScript use, crawl restrictions, and completion status. It never substitutes a guessed address.

One school failure does not stop the batch. Human-readable status remains in the card; lower-level details are contained in a collapsed technical section. Use **Retry This School**, provide a better official URL, or retry all unresolved schools without restarting completed work.

## Exports

The app provides:

- All results as CSV
- All results as Excel
- Verified contacts only as CSV
- Unresolved schools as CSV

The Excel workbook contains separate sheets for Verified Contacts, General Contacts, Needs Review, Unresolved Schools, and Search Summary. Header rows are frozen, filters are enabled, and column widths are adjusted for readability.

## Tests

Tests are entirely local and use sample HTML plus mocked network responses; they do not repeatedly call school websites.

```text
python -m pytest -q
```

Coverage includes normalization and abbreviation expansion, ambiguous matching, visible/`mailto:`/obfuscated/structured email evidence, rejection of incomplete and placeholder addresses, role detection and false-positive rejection, name-role-email association, deduplication, general department separation, domain validation, third-party rejection, grouped processing, failure isolation, manual website override processing, and Excel generation.

## Project structure

```text
app.py                         Streamlit interface and session workflow
src/models.py                  Typed data models
src/nj_school_directory.py     NJDOE directory loading and cache
src/result_cache.py            Persistent completed school-result cache
src/school_matcher.py          Conservative normalization and matching
src/website_finder.py          Ordered official-site discovery
src/website_validator.py       Domain and content validation
src/crawler.py                 Robots-aware, rate-limited site crawl
src/html_parser.py             Page structure, link, and contact parsing
src/pdf_parser.py              Text-based PDF extraction
src/role_matcher.py            Role categories and false-positive rules
src/email_extractor.py         Exact public-email evidence extraction
src/result_processor.py        Orchestration, confidence, and deduplication
src/exporters.py               CSV and formatted Excel exports
tests/                         Offline automated tests
data/                          Optional local source data
cache/                         Download and page cache
```

## Troubleshooting

**`streamlit` is not recognized**  
Activate the virtual environment, or run `python -m streamlit run app.py` using that environment's Python executable.

**The JavaScript fallback reports no browser executable**  
Run `python -m playwright install chromium` inside the activated environment. Normal HTML/PDF crawling continues even without Chromium when the fallback is disabled.

**An NJ school is missing while offline**  
Refresh the bundled/current NJDOE catalog when online, choose **Retry School Identification**, or configure a newer NJDOE-derived CSV/XLSX with `NJ_SCHOOL_DIRECTORY_FILE`.

**A school website is marked inconclusive**  
Review the candidate in a browser. Confirm it only when it is genuinely operated by the selected school or district, or paste the known official URL.

**The website blocks or rate-limits the crawl**  
The app will not bypass that restriction. Try later, provide a more specific official department page, or use the visible official directory manually.

**A scanned PDF has no results**  
The app intentionally does not run large-scale OCR. Open the linked PDF for manual review.

**A contact known to exist is missing**  
The app searches every supported role category with JavaScript and PDF support automatically. Retry the school, or provide the more specific official school/department page as a website override.

## Known limitations

- Public search providers and NJDOE pages can change markup, throttle requests, or become temporarily unavailable.
- Some school-directory platforms expose data only after complex interaction or behind bot protection; the app does not evade those controls.
- Session-state results survive Streamlit reruns but not a full server restart.
- A visible email can be outdated even when the source is official; always check the linked source and checked time.
- Scanned/image-only PDFs require manual review.
- The app deliberately favors false negatives over incorrectly associating an email with a person or school.
