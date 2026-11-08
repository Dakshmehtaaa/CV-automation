# CV Studio

A private browser workspace for the existing CV pipeline. It reads the canonical
files in `Resources` and the existing `Tailored CVs` folders. All interface assets
are bundled locally; browsing records, previewing PDFs and editing sources need
no internet connection.

## Open it

Double-click **Resources → Open CV Studio.command**. The launcher opens Google
Chrome and reuses the existing Studio server when it is already running.
The default address is `http://127.0.0.1:8767`; another free local port is selected
if needed. The address printed by the launcher is the actual one.

From a terminal in the CV folder:

```sh
python3 Resources/Studio/launch.py
```

Python 3.9 or later is sufficient for the app. No Node installation, web hosting,
API key, CDN or frontend build step is needed. The existing pipeline keeps its
own document-rendering and model dependencies.

## Inside the workspace

- **Home** — Applied first, a visual search funnel, source outcomes, automation performance,
  a language donut, standardised locations, and a saved-JD requirements explorer. One Paris-time
  filter controls Today, Yesterday, This week, Last week, This month and All time.
  The next-step card shows the current ready-to-apply queue independently of that date filter.
  Archive evidence checks run in one background worker; pending totals are shown as unknown.
  Refreshes retain keyboard focus and chart scroll positions, and hidden tabs pause polling.
  Shared appearance and motion rules live in `static/studio-polish.css`, with light/dark,
  reduced-motion, reduced-transparency and increased-contrast support.
- **Apply** — three job cards across on laptop screens and four on large monitors,
  larger company logos, a corner CV preview icon, compact ratings and side-by-side
  copy/contact controls. Its sidebar badge always shows jobs remaining, including zero.
  Add a URL, pasted JD or both to the local manual-build queue without calling a model.
- **CVs** — search, filter and inspect CVs and letters.
- **Contacts** — saved verified emails, LinkedIn details, original public sources,
  completed empty searches and cleanup status. Completed searches cannot be started again.
- **Prospect** — the complete Prospector workspace: Consulting and Job search modes,
  Today, Find, Leads, Learn and its own Settings. Its files now live in `CV/Prospector`.
  Opening this area starts its local service on demand; it does not start research.
  Saved records, suppression rules, drafts and runner settings remain in Prospector.
  Studio refreshes and navigation preserve the open Prospect workspace. **Open full view** opens it
  in another tab. The old Prospector folder is a compatibility link to the new one.
  Its **Applied** page reads Studio's `/api/roles` (Studio passes its address as
  `CV_STUDIO_URL`) and turns each application into find-the-person links, fixed follow-up
  drafts and a follow-up log kept in `Prospector/data/applied.json`. It never writes to Studio.
- **History** — job outcomes, accepted shortlist counts, CV results and time spent,
  with seven-step timings, models, usage and failures available below. Running Studio
  jobs retain their logs, questions and Stop control here.
- **Profile & search** — edit the profile and search brief, preview the text, and save
  with revision checking and a backup. Frozen workflow sections and the CV bank
  mirror remain protected. The wording bank, design guide and routing are readable.
- **Settings** — appearance, refresh, new-run model/effort and Reset to defaults.

Apply has three compact readiness rings out of five: saved advert English, existing
profile fit and estimated company English friendliness. Click a rating for its
reason and available company sources. Unknown company evidence stays unscored.
Ratings use saved local data; page refreshes do not call a model or research tool.
Application choices save on this Mac and never submit an application.

Prospector's original `start.sh` now opens Studio directly at **Prospect** when it is
inside this CV folder. `python3 server.py` inside Prospector still serves the standalone
app. A local ownership lock prevents two servers from writing the same Prospector data.
Integrated startup preserves saved records without running the standalone retention sweep.
The separate APIs retain their existing write guards and local-origin checks.
The standalone Pipeline and Beta Home pages are removed. CVs has no sidebar count.
Extra choices include Saved for later, Contact to find, Interviewing, Offer,
Rejected and Withdrawn. Filters cover status, language, documents, profile fit,
contract and advert source, with newest/oldest/company/fit ordering.

The single motivation button follows the selected cover-letter language, reads saved wording, opens a preview with its source,
and offers an explicit copy action. Only a missing language starts generation:
the bounded task requests signed-in GPT-6 Luna at High effort and Fast service
tier, using the saved advert, existing wording and relevant approved facts.
Requested settings are shown separately from provider-reported settings. Generated
text stays local, follows the 380–600 character rule, and must be reviewed before
copying. It does not modify approved CVs or letters.

The contact action starts one Luna MAX public search, followed by deterministic
source checks. It also checks official customer-service, after-sales and support
pages for directly published company email addresses. It never guesses addresses.
Results need a public source and a check date; named people also need a professional role.
The server checks quoted evidence and the actual email/LinkedIn value against the
source page before displaying it. Missing or unverifiable contact details remain
absent. Tasks never send messages, apply, read the private inbox or change the
online tracker. Refreshes read saved results; they do not silently start new
research. Local backups preserve earlier saves.

Search shows an animated progress indicator and an explicit Stop button. A successful
zero-contact result still counts as a completed search. A bottom-left status notice
stays until dismissed and opens the saved results, including an empty result. Saved emails survive a later
result without an email, and read-only recovery accepts only already approved saved
results or backups. Missing contact evidence stays absent.

The Home JD explorer uses deterministic Python extraction, with literal quotes and
source references for IT skills, soft skills, languages, tasks, other terms and years
of experience. A mention is not automatically a mandatory requirement. Saved full
captures are hash checked; incomplete captures are omitted and coverage is shown.
Unknown dates are excluded from scoped periods rather than assigned today's date.

Saved jobs are frozen into the next ordinary pipeline run before Hunt. Supported
source links get a fresh capture, duplicate and fit checks, and central Hunt Audit
before Build; edited inputs and unsupported sources remain queued for review.
Build now is a discreet button on each queued item and
refuses admission while the current pipeline owns the workspace. It requests Luna
MAX for writing and a distinct Sol reviewer for tests and actual preview inspection.
A successful draft stays under review until final approval and sync; it does not
automatically become ready to apply. New runs record durable task start/finish events
and readable INFO durations for native worker stages, observed Hunt tool intervals
and supervised commands. Missing observations remain unknown; older run clocks
are never reconstructed. See NEXT.md for the latest verification checkpoint.

A contact route is unavailable if its runner cannot restrict access to public
research. Luna must pass a
private-tool isolation check before an application task starts.

Opening the raw `static/index.html` redirects to the default local server.
Use the launcher if that server is not running or uses another port.

New manual and scheduled CV Pipeline runs record History checkpoints using
`Master/auto/local/run_metrics.py` and `RUN_HISTORY.md`. Seven-step receipts
preserve actual dispatch/finish, configured versus observed models, available
usage, outcome counts and failures. Each CV clock ends at its recorded artifact
completion; later audit and sync time stay separate. This instrumentation uses
the established pipeline's evidence and does not rewrite historical runs.

Studio also saves atomic local history snapshots under `.local/history/runs/`.
These preserve normalized run facts and job outcomes when original receipts are
missing or temporarily incomplete. A recovered view is labelled with its saved
time and a warning. Raw reports, inbox contents and coordinator messages are not
copied into these snapshots. Draft proposals and independently accepted roles
have separate counts; missing evidence remains unknown.

### Job feed audit on Home

Home ends with a "Job feed audit" section (Today / Week / Month / All): per-feed collection time,
accuracy as rated by Hark, report-email and mailbox counts, and a feed-health table. It reads
`Studio/.local/hark-kpi-audit.json`, a local copy of the Hark sheet's **Audit KPIs** tab (Hark's 12:00
Job Feed Audit adds one row per day). Studio cannot call Google, so a Claude session refreshes the copy:
read the tab with the Sheets connector (`Audit KPIs!A1:CX`) and pipe the rows into

```sh
python3 Resources/Studio/hark_kpi.py import rows.json   # or: ... import < rows.json
```

The file is rebuilt from the whole tab each time; nothing is written back to the sheet. Mailbox counts
(rejections, job suggestions) only exist in Hark's free-text note, so they show as "not recorded" for days
Hark did not write them. Tests: `python3 -m unittest test_hark_kpi`.

## Running work

**Check local files** invokes the existing `Master/preflight.py --all --compact`
gate. It runs the established checks; it does not approve or submit applications.

**Check connections** reads signed-in Codex model and connector metadata. It does
not read inbox messages or generate model output. Listed models and callable
tools are distinguished from a verified generation or completed pipeline.

**Run pipeline** starts the current `SCHEDULED_PROMPT.md` workflow in a separate,
Studio-owned Codex app-server session. Manual and scheduled starts share policy-4 routing, source and
factual checks, independent audits, approval gates and original batch clock.
Live research, connected read-only Gmail and hosted models need internet.
Final pipeline sync uses this local CV Studio; Google Sheet is retired for new runs.
The browser shows output and any permission or input request from that session.
Stop is available for Studio-owned work and a freshly verified local desktop
pipeline turn. The confirmation identifies the run; unavailable scope remains
unavailable rather than targeting unrelated work.

An external batch's open window or running worker blocks another launch and
source edits. Historical status labels are never treated as proof of live work.
Studio records coordinator completion separately from pipeline completion.
Provider errors, exhausted limits, interruption or uncertain remote cleanup keep
unfinished work visibly incomplete and prevent an unsafe follow-up launch.
Studio does not buy credits, reset quotas, change models or retry jobs silently.

The implementation uses the existing signed-in Codex installation and the
[official app-server interface](https://learn.chatgpt.com/docs/app-server).
It does not create another schedule or replace the existing pipeline engine.

## Local storage and privacy

Studio binds only to the loopback address. It validates request origins and a
session token for writes. File previews use registered PDF keys and containment
checks; arbitrary filesystem paths are not accepted.

Interface preferences, edit backups, the edit journal and Studio job logs live in
`Resources/Studio/.local/`, which is ignored by Git. Existing pipeline receipts
and CVs remain in their established locations. Closing the browser does not stop
a job. Keep the Mac awake and the Studio process running while work is active.

If a job says **recovery required**, Studio has not been able to prove that all
of its work stopped. Keep the job log and its evidence folder. Ask Codex to review
that specific Studio job, verify its owned local processes and remote receipts,
and close the original work within its original clock. There is deliberately no
button that bypasses this check or marks the job successful.

## Development checks

From `Resources/Studio`, run:

```sh
python3 -m unittest discover -p 'test_*.py' -v
```

The tests use isolated fixtures. They do not run a production Hunt, touch the
online tracker, send messages or change the live schedule.

The full interface check uses Google Chrome and the workspace's existing
Playwright installation:

```sh
node test_browser.cjs
```

Set `CV_PLAYWRIGHT` to another `playwright-core` installation or `CV_PYTHON` to
another Python executable if needed. This test creates a temporary workspace,
checks editing, previews, run controls, limits and mobile layouts, then removes
the fixture. Results and screenshots are saved under `.local/qa/`.

## Find email (5 Nov 2026)
Sidebar button "Find email", under Mercury 3. Type a name, LinkedIn link or company; pick Sonnet 5.5 (default) or Haiku 5.5.
It runs the local `claude` CLI with only WebSearch + WebFetch (empty temp folder, no file access) using
`prompts/find-email.md`, then `email_finder.py` checks each email: no free-mail, domain has mail servers (dig MX),
published emails really appear on their page, pattern guesses need 2+ real colleague emails (3+ for High).
"Draft email" (always Sonnet 5.5) uses `prompts/outreach-email.md` + PROFILE.md and adds `prompts/email-signature.html`.
Nothing is sent: copy the email + signature and paste it into Gmail. Logs: `.local/email-finder/`.
Backup before this change: Backups/studio-email-finder-20261105/.

## Outreach page (5 Nov 2026, replaces Shortlist + Contact library)
`static/outreach.js` + `outreach.css`, mounted from app.js for the `shortlist` / `contacts` routes. One screen:
counters (follow-ups due, drafts to send, waiting, replied), "Reach someone" (LinkedIn link or name + optional linked
job → Sonnet 5.5 finds the work email, then drafts automatically), Today (follow-ups due after 5 days, drafts not sent),
People list (filters, Write / Follow-up / Replied / Archive), and a writer panel (editable subject + body, FR/EN, job,
Copy email + signature, Open Gmail, "✓ I sent it", CV / cover-letter download to attach, LinkedIn note).
Prompts: prompts/outreach-email.md (now with the linked job + LinkedIn note), prompts/outreach-followup.md (max 2).
Data: .local/email-finder/contacts.json (shared with the Find email drawer). Routes: /api/email-contacts (GET, POST
{key, status} or {key, fields}), /api/find-email, /api/draft-email (+role_id), /api/followup-email.
Ideation: plans/outreach-revamp/ (9 Gemini 3.8 Flash agents via Antigravity; r3-final-spec.md + r3-critic.md).
Not built (later): Gmail draft push with the PDF attached (needs Gmail API OAuth on the Mac), reply auto-detection.
Backup before this change: Backups/studio-outreach-desk-20261105/.
