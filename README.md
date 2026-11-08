# CV Automation (CV Studio)

Free, open-source tooling for automating a job hunt: a local browser workspace
(CV Studio) that helps a job seeker track applications, tailor CVs and cover letters
per job, score fit offline, find public contacts and draft outreach. Runs on your own
computer. No hosting, no account, no data leaves your machine except calls to the
AI model you choose.

Aim: let non-technical job seekers get what developers get from automation,
without writing code.

## Status: early and honest

- New project, built by one person for their own job search, now being opened up.
- Small reach so far: no external users yet beyond the author.
- This repo holds the **Studio layer** (Python server, local web UI, tests).
  The CV build pipeline it reads from (`Master/`, `PROFILE.md`, tailored-CV folders)
  is **not included yet**. Some tests need it (for example `advert_inventory`).
- Code was extracted from a personal workspace. Personal data, keys and
  application files were removed. Expect rough edges and hard-coded
  assumptions (French job market, Paris time zone). Issues and PRs welcome.

## Run

Needs Python 3.9+. No Node, no build step (the compiled frontend is in `static/`).

```sh
python3 launch.py
```

Opens at `http://127.0.0.1:8767` (another free port if busy).

## Test

```sh
python3 -m unittest discover -p 'test_*.py'
```

## Privacy and safety

- Local only (`127.0.0.1`). Studio never sends emails or submits applications.
- API keys are read from your own OS keychain or environment. None are stored here.
- `.local/` (your data) is git-ignored.

## More

See [docs/STUDIO_GUIDE.md](docs/STUDIO_GUIDE.md) for the full feature tour.

## License

MIT, see [LICENSE](LICENSE).
