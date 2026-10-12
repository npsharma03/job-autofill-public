# Job Autofill

A local Python tool that fills recognized job application fields using your profile, uploads your resume, and tracks applications.

**You review the answers and click Submit yourself.**

## 1. Download and install

Install Python and Git, then run:

```powershell
git clone https://github.com/npsharma03/job-autofill-public.git
cd job-autofill-public
pip install -r requirements.txt
python -m playwright install chromium
```

Alternatively, download the repository through **Code → Download ZIP**, extract it, and open a terminal in the extracted folder before running the installation commands.

## 2. Set up your profile

On Windows PowerShell:

```powershell
Copy-Item profile.example.yaml profile.yaml
notepad profile.yaml
```

On macOS or Linux:

```bash
cp profile.example.yaml profile.yaml
```

Edit `profile.yaml` with your details. For example:

```yaml
personal:
  first_name: "Jane"
  last_name: "Doe"
  email: "jane@example.com"
  phone: "555-123-4567"
  city: "Chicago"
  state: "Illinois"
  country: "United States"
  location: "Chicago, IL"

links:
  linkedin: "https://www.linkedin.com/in/your-profile/"
  github: "https://github.com/your-username"
  website: ""

work:
  current_company: ""
  current_title: ""
  years_experience: ""
  expected_salary: ""
  start_date: ""
  pronouns: ""
  how_heard: ""

answers:
  work_authorized: ""
  needs_sponsorship: ""
  willing_to_relocate: ""

files:
  resume: "./resume.pdf"
  cover_letter_pdf: ""
```

Keep any additional fields from the example profile that you want to use.

- Blank values (`""`) leave fields for you to answer manually.
- Enter your own authorization, sponsorship, and demographic answers.
- For yes/no questions, use quoted `"Yes"` or `"No"`.
- For date fields, use `YYYY-MM-DD`, such as `"2027-01-15"`.
- Preserve YAML indentation using spaces.

## 3. Add your resume

Place your resume beside `autofill.py` and set its path in `profile.yaml`:

```yaml
files:
  resume: "./resume.pdf"
  cover_letter_pdf: ""
```

The filename is configurable:

```yaml
files:
  resume: "./Jane_Doe_Resume.pdf"
  cover_letter_pdf: ""
```

You can also use a full path. On Windows, forward slashes avoid YAML escaping problems:

```yaml
files:
  resume: "C:/Users/YourName/Documents/Jane_Doe_Resume.pdf"
  cover_letter_pdf: ""
```

Relative paths are resolved from the folder containing `autofill.py`. PDF is a practical default, but the application website determines which formats it accepts.

The script uploads your resume when it recognizes a resume upload field. **It does not extract your details from the resume:** fill in `profile.yaml` separately.

## 4. Run an autofill request

For a single application, use this exact command format:

```powershell
python autofill.py --url "JOB_APPLICATION_URL"
```

Replace `JOB_APPLICATION_URL` with the actual job link. Keep the URL in quotes, especially if it contains query parameters.

**Include `--url`; the script does not accept a bare URL argument.**

To use a different profile:

```powershell
python autofill.py --profile "another-profile.yaml" --url "JOB_APPLICATION_URL"
```

## 5. Run multiple applications

Edit `jobs.txt`. Add one job per line using either format:

```text
URL
URL | Company | Role
```

For example:

```text
https://jobs.lever.co/examplecompany/example-job-id | Example Company | Data Scientist
https://job-boards.greenhouse.io/examplecompany/jobs/1234567 | Example Company | ML Engineer
```

These are illustrative links; replace them with real job URLs.

Blank lines and lines beginning with `#` are ignored. Company and role are optional, but supplying them avoids relying on automatic detection.

Run the list:

```powershell
python autofill.py
```

Or use another job list:

```powershell
python autofill.py --jobs "my-jobs.txt"
```

## 6. Review and submit

The script opens a visible Chromium browser and attempts to fill recognized fields.

- **Green outlines:** fields filled by the script.
- **Red outlines:** required fields detected as still empty.

Review every answer, complete remaining fields, and click **Submit** on the website.

Then return to the terminal:

| Input | Action |
|---|---|
| Enter | Record that you submitted this application and continue |
| `r` | Refill the current page |
| `s` | Skip this application |
| `q` | Quit |

Only press Enter after the website confirms successful submission. The script records your response; it does not verify submission.

For multi-step applications, advance manually and use `r` to fill the next page.

## 7. Optional cover letter

Edit `cover_letter.txt` with your own text. Supported placeholders include:

```text
Hi {company} team,

I'm interested in the {role} position.

[Your relevant experience and reason for applying.]

Best,
{first_name} {last_name}
{email} | {linkedin}
```

The script substitutes the company, role, and profile values when filling recognized cover letter text fields. Replace any bracketed instructions yourself; it does not generate tailored content.

For a cover letter upload, configure a separate file:

```yaml
files:
  resume: "./resume.pdf"
  cover_letter_pdf: "./cover_letter.pdf"
```

## Additional options

```powershell
# Replace fields that already contain values
python autofill.py --url "JOB_APPLICATION_URL" --overwrite

# Include jobs previously recorded as submitted
python autofill.py --redo
```

By default, existing field values are preserved and jobs logged as submitted are skipped.

## Application history

The script creates `applications.csv` when it first logs an application:

```csv
timestamp,url,company,role,status
```

Statuses reflect your terminal input: `submitted`, `skipped`, or `quit`.

## Privacy

Your configuration is read locally, and configured information is entered into application websites.

The script automatically creates `.browser-profile` on first run. This folder preserves browser state and may contain login cookies and session data.

Keep personal files out of Git:

```gitignore
.browser-profile/
profile.yaml
applications.csv
resume.pdf
cover_letter.pdf
__pycache__/
.venv/
```

Add any custom resume filenames, personal cover letters, and other profile files to `.gitignore` too. Ignore rules do not remove files already tracked by Git.

## Limitations

Field matching is heuristic. Some websites, dropdowns, and multi-step forms require manual input. Review all answers before submitting, especially authorization and sponsorship answers.

Websites may show verification errors or reject automated browser sessions. This tool does not bypass CAPTCHAs or automatically submit applications.
