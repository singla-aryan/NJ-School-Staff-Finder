# NJ School Student-Support Staff Finder

This website finds publicly listed professional contact information for school counselors, psychologists, social workers, therapists, student-assistance staff, and related student-support professionals at New Jersey schools.

Paste one school name per line. The app identifies the school and district, locates the official school or district website, and searches it for relevant staff. It reports only addresses displayed publicly on official sites and never guesses email addresses.

## Public app

- Streamlit Community Cloud: <https://nj-school-staff-finder.streamlit.app/>
- A Hugging Face Spaces version can be deployed from the included `Dockerfile`.

## Run locally on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py
```

After installation, you can instead double-click `Run School Finder.bat` or open `run.py` and press **Run**. The launcher prints the local browser link and opens it automatically.

## Run locally on macOS or Linux

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

## Hosting

`packages.txt` supports Streamlit Community Cloud. The `Dockerfile` runs the same app on port `7860` for a Hugging Face Docker Space and installs Chromium for JavaScript-powered directories.

Runtime page caches and saved-result files are temporary on both free hosts and can disappear after a restart or redeployment. The bundled NJDOE school catalog remains available because it is included in the repository.

## Responsible use

The app searches only publicly accessible official school and district websites. It respects `robots.txt`, uses conservative per-domain request limits, and does not bypass authentication, CAPTCHAs, or access restrictions. Public contact information should still be used responsibly and in accordance with applicable policies and law.

## Test

```bash
python -m pytest -q
```
