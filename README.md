# Opportunity & Startup Intelligence Scout

Three AI scouting agents that search the web every morning, verify what they find against the original sources, and send **one Telegram digest at 10:00 IST**. Everything runs on GitHub Actions, so it keeps working when your computer is off.

| Agent | Looks for |
|---|---|
| **1. Civil internships** | Industrial, research and training internships for Dec 2026 – 2027, focused on PEB/prefab, structural/steel, construction management, EPC/infrastructure, BIM, and IIT/NIT/IISc/CSIR research. South India gets priority. Eligibility is checked for a foreign-national, final-year B.Tech student. |
| **2. Startup programmes** | Free or fully funded courses, certificates, bootcamps, accelerators, incubators, competitions, hackathons, grants and government schemes in India. Fees, certificates, equity, deadlines and foreigner eligibility are stated explicitly. |
| **3. Startup intelligence** | Funding rounds, valuations, growth, down-rounds and shutdowns, in India and globally. Priority sectors are AI, EdTech, ConTech, PropTech, AgriTech and GIS. Each item covers founders, model, revenue and investors, plus an AI analysis labelled as such. |

The daily message has four sections: **internships → free programmes → startup stories → upcoming deadlines and recommended actions**. If a section has nothing new, it says what was checked instead of repeating old items.

Bot commands: `/internships` `/courses` `/startups` `/deadlines` `/sources` `/status` `/help`

---

## How it works

```
GitHub Actions (cron, UTC)
 ├─ daily.yml   09:13 IST  restore DB → 3 agents → wait until 10:00 IST → send digest → save DB
 │              10:43 IST  fallback: sends only if today's digest is missing
 └─ bot.yml     every 15 min: read DB → answer Telegram commands

Each agent:  Tavily search + official pages (watchlist) + RSS
             → skip URLs already processed → fetch page (robots.txt, rate-limited; HTML and PDF)
             → Claude Haiku 5.5 extracts structured fields, each with a verbatim quote
             → verify: quotes must appear in the page, dates must match their quotes,
               links must appear in the page; otherwise the field is dropped and flagged
             → filter (expired, Indian-nationals-only, not for final-year students,
               outside your dates, paid) → dedupe (URL, fingerprint, fuzzy) → score
             → Claude Opus 5.5 ranks the new items and writes "why it matters" and
               "next action", plus the growth story, edge and lessons for startup stories
SQLite (data/scout.db) is kept on the bot-managed `scout-data` branch between runs.
```

Code map: `scout/agents/` (the three agents and the shared pipeline), `scout/sources/` (Tavily, RSS, fetching, YouTube), `scout/verify.py`, `scout/dedupe.py`, `scout/rank.py`, `scout/llm.py`, `scout/report.py`, `scout/delivery.py`, `scout/bot.py`, `scout/db.py`. Search queries, official pages, feeds, score weights and manual-check links are in `config/*.yaml`. Your profile is in `config/profile.yaml`.

---

## One-time setup (about 15 minutes)

1. **Create the Telegram bot.** In Telegram, open **@BotFather** → `/newbot` → choose a name. Copy the **bot token**, then open your new bot and press **Start**.
2. **Get a Tavily key (free).** Sign up at <https://app.tavily.com>. The free tier gives 1,000 credits a month with no card. This system uses about 24 a day.
3. **Get an Anthropic API key.** At <https://platform.claude.com> → API keys, create a key and add a little credit; API billing is separate from a Claude.ai subscription. Set a monthly spend limit in the Console as a safety net.
4. **Add repository secrets.** In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `ANTHROPIC_API_KEY`
   - `TAVILY_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
5. **Find your chat ID.** Go to **Actions → Telegram commands → Run workflow**, choose mode `whoami`. The bot replies in Telegram with your chat ID, so it never appears in the public logs. Add it as a secret named `TELEGRAM_CHAT_ID`.
6. **Test the bot.** Run **Telegram commands** again with mode `send-test`. You should get a test message, and the command menu gets registered.
7. **Do a dry run.** Go to **Actions → Daily digest → Run workflow** with `dry_run` ticked. Open the run's **Summary** to see the digest preview and per-agent stats. Nothing is sent.
8. **Go live.** Run **Daily digest** with `dry_run` unticked to send today's digest. After that, the schedule runs it every day.

Optional: add `YOUTUBE_API_KEY` (YouTube Data API v3, free quota) for video results. You can also set **repository variables** (not secrets) to override the defaults below.

### Configuration reference

| Name | Kind | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | secret | — | Claude API (extraction + analysis) |
| `TAVILY_API_KEY` | secret | — | Web search |
| `TELEGRAM_BOT_TOKEN` | secret | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | secret | — | The only chat the bot will talk to |
| `YOUTUBE_API_KEY` | secret | unset | Optional YouTube search |
| `SCOUT_EXTRACT_MODEL` | variable | `claude-haiku-5-5` | Bulk page extraction |
| `SCOUT_ANALYSIS_MODEL` | variable | `claude-opus-5-5` | Ranking, advice, startup analysis |
| `SCOUT_MAX_LLM_USD_PER_MONTH` | variable | `12` | Stops Claude calls when reached |
| `SCOUT_MAX_TAVILY_PER_DAY` | variable | `30` | Stops searches when reached |

---

## Schedule and timing

- GitHub cron runs in UTC, and IST is UTC+5:30 with no daylight saving, so 10:00 IST = 04:30 UTC.
- GitHub can start scheduled jobs late (often 5–30 minutes) and occasionally drops one. To cover this, scouting starts at **09:13 IST** and the job **waits until 10:00 IST** before sending. A **10:43 IST fallback** run sends the digest only if today's is missing.
- **Expect delivery between 10:00 and ~10:15 IST.** For to-the-minute delivery, add a free external trigger: a [cron-job.org](https://cron-job.org) job at 09:15 IST that calls `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/daily.yml/dispatches` with body `{"ref":"main","inputs":{"dry_run":"false","wait":"true"}}`. It needs a fine-grained token with *Actions: read and write* on this repo.
- **Duplicate prevention:** only one digest per IST date. Each part's Telegram message ID is recorded as it is sent, so a retry after a partial failure sends only the missing parts. Items are announced once, and deadline reminders fire once per threshold (14/7/3/1 days).
- Public repos have their schedules disabled after 60 days without activity. The daily job re-enables both workflows on every scheduled run to prevent this.
- Commands are answered by polling every 15 minutes, so replies can take a few minutes. Updates are acknowledged only after the reply is sent. The bot ignores every chat except `TELEGRAM_CHAT_ID`.

## Verification rules ("never invent information")

- Every deadline, stipend, fee, funding amount, valuation, revenue and eligibility statement needs a **verbatim quote** that is found in the fetched page. If not, the value is removed and the item shows *"not confirmed in source"*.
- Dates must also match their own quote. Application links are trusted only if they appear in the page.
- Valuations are labelled **confirmed** (company/investor/filing), **reported** (media citing sources) or **estimated**. Funding and revenue are always kept separate. Growth stories and lessons are marked *"Analysis (AI, not reported fact)"*.
- Items found only through a search snippet (for example LinkedIn) are marked *"search snippet only — open the link to verify"*.
- Expired listings, Indian-nationals-only listings, listings not open to final-year students and listings outside Dec 2026 – 2027 are kept out of the digest. They are counted in the status line.

## Source limitations (and what to check manually)

| Source | Access used | Limitation |
|---|---|---|
| Official company / institute pages | Direct fetch (robots.txt respected) | JavaScript-only pages may yield little text; failures are listed under `/sources` |
| General web | Tavily search API | 1,000 credits/month free; results are search-engine snippets until fetched |
| LinkedIn | Search-engine snippets only | Automated access is against LinkedIn's terms, so pages are never fetched |
| Instagram | None | No permitted public search API; manual link in `/sources` (login required) |
| YouTube | Official Data API (optional key) | Video descriptions only |
| News (Inc42, Entrackr, YourStory, ET, TechCrunch, Crunchbase News…) | RSS feeds | Paywalled articles give headline + summary only |
| Valuations / revenue | As reported in articles | Tracxn, Crunchbase and MCA filings need accounts; linked in `/sources` for cross-checks |

`/sources` lists the manual-check links (LinkedIn job searches, Internshala, Unstop, Instagram, NPTEL) and any official page that failed on the last scan.

## Operating costs (estimates)

| Item | Cost |
|---|---|
| GitHub Actions (public repo) | Free |
| Tavily (~24 credits/day ≈ 720/month) | Free tier |
| Telegram Bot API, RSS feeds | Free |
| Claude Haiku 5.5 extraction (~60 pages/day) | ≈ $0.04/day |
| Claude Opus 5.5 analysis (3 calls/day) | ≈ $0.20–0.30/day |
| **Total** | **≈ US$5–10 per month** (hard cap: `SCOUT_MAX_LLM_USD_PER_MONTH`, default $12) |

`/status` shows Tavily credits and estimated Claude spend for the month. Costs fall over time because URLs already processed are never re-extracted.

## Data and privacy

- The SQLite DB lives on the `scout-data` branch (one commit, overwritten each run). A 30-day backup is attached to each daily run as an artifact.
- This repo is **public**, so the DB, workflow logs and `config/profile.yaml` are publicly readable. They contain only public listings and the digest text. Secrets and your chat ID are never stored or logged. Make the repo private if you prefer; that gives 2,000 free Actions minutes/month, which is enough if the poller runs every 30 minutes instead of 15.

## Customising

- **Profile:** edit `config/profile.yaml`. Set your nationality, preferred regions and the internship window.
- **Searches, official pages, feeds and weights:** edit `config/internships.yaml`, `config/programmes.yaml` and `config/startups.yaml`. Each Tavily query costs 1 credit per day.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                        # offline tests (no API keys needed)
export ANTHROPIC_API_KEY=... TAVILY_API_KEY=...
python -m scout scout internships       # run one agent, print new items
python -m scout daily --dry-run         # full run, print the digest instead of sending
python -m scout report internships      # print what /internships would reply
```

## Troubleshooting

- **No digest arrived.** Check **Actions → Daily digest** for today's runs and read the job summary. Missing secrets are listed in the job summary, and a scheduled run fails straight away if the Telegram ones are absent. If the schedule shows as disabled, click **Enable workflow**.
- **The bot doesn't answer commands.** Check that `TELEGRAM_CHAT_ID` matches the ID from `whoami`, and look at the latest **Telegram commands** run.
- **Too many or too few items.** Adjust the queries and weights in `config/*.yaml`, or `min_relevance` in the agent classes.
