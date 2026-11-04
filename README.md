# Opportunity & Startup Intelligence Scout

Three AI scouting agents that search the web every morning, verify what they find against the original sources, and send **one Telegram digest at 10:00 IST**. Everything runs on GitHub Actions (plus an optional free Cloudflare Worker for on-time delivery and instant bot replies), so it keeps working when your computer is off.

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
Cloudflare Worker (free, step 9; recommended)
 ├─ cron 09:15 IST  → starts daily.yml on time      10:45 IST fallback start
 └─ Telegram webhook → answers commands in seconds from replies.json

GitHub Actions
 ├─ daily.yml   restore DB → 3 agents → wait until 10:00 IST → send digest
 │              → save DB + replies.json (rendered command replies)
 │              Also on GitHub's own schedule (09:13 / 10:43 IST), which often starts hours late;
 │              any run after the digest went out exits at once.
 └─ bot.yml     every 15 min: read DB → answer Telegram commands (only used without the Worker)

Each agent:  Tavily search + official pages (watchlist) + RSS
             → skip URLs already processed → fetch page (robots.txt, rate-limited; HTML and PDF)
             → a small AI model extracts structured fields, each with a verbatim quote
               (Gemini 3.5 Flash-Lite on the free tier, or Claude Haiku 5.5)
             → verify: quotes must appear in the page, dates must match their quotes,
               links must appear in the page; otherwise the field is dropped and flagged
             → filter (expired, Indian-nationals-only, not for final-year students,
               outside your dates, paid) → dedupe (URL, fingerprint, fuzzy) → score
             → a stronger model (Gemini 3.6 Flash, or Claude Opus 5.5) ranks the new items and writes "why it matters" and
               "next action", plus the growth story, edge and lessons for startup stories
SQLite (data/scout.db) and replies.json are kept on the bot-managed `scout-data` branch.
```

Code map: `scout/agents/` (the three agents and the shared pipeline), `scout/sources/` (Tavily, RSS, fetching, YouTube), `scout/verify.py`, `scout/dedupe.py`, `scout/rank.py`, `scout/llm.py` (Claude), `scout/gemini.py` (Gemini), `scout/report.py`, `scout/delivery.py`, `scout/bot.py`, `scout/db.py`, and `worker/index.js` (the Cloudflare Worker). Search queries, official pages, feeds, score weights and manual-check links are in `config/*.yaml`. Your profile is in `config/profile.yaml`.

---

## One-time setup (about 15 minutes, all free)

You need four things: a Telegram bot, a Gemini key, a Tavily key, and your chat ID. None of them needs a credit card.

**Step 1. Make your Telegram bot.**
Open Telegram and search for **@BotFather**. Send `/newbot`. Type a name, then a username that ends in `bot`. BotFather replies with a long **token** (it looks like `123456:ABC-xyz...`). Copy it somewhere safe. Then search for your new bot, open it and press **Start**.

**Step 2. Get a free Gemini key (this is the AI).**
Go to <https://aistudio.google.com/apikey> and sign in with your Google account. Click **Create API key**, accept the terms, and copy the key (it starts with `AIza`). Do not turn on billing; the free tier is enough.

**Step 3. Get a free Tavily key (this is the web search).**
Go to <https://app.tavily.com> and sign up. Copy the API key from the dashboard (it starts with `tvly-`). The free plan gives 1,000 searches a month, and this system uses about 24 a day.

**Step 4. Put the keys into GitHub.**
Open this repository on GitHub. Click **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add these three, one at a time (the name must match exactly):

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | the key from step 2 |
| `TAVILY_API_KEY` | the key from step 3 |
| `TELEGRAM_BOT_TOKEN` | the token from step 1 |

**Step 5. Find your chat ID.**
Click the **Actions** tab → **Telegram commands** (left side) → **Run workflow**. Choose mode `whoami` and click the green **Run workflow** button. After a minute your bot sends you a message with your **chat ID** (a number). Add it as one more secret named `TELEGRAM_CHAT_ID`, the same way as step 4.

**Step 6. Send a test message.**
Run **Telegram commands** again, this time with mode `send-test`. You should get a "test message" in Telegram. If not, check the token and chat ID secrets.

**Step 7. Do a practice run (nothing is sent).**
Click **Actions** → **Daily digest** → **Run workflow**. Leave `dry_run` ticked and click **Run workflow**. It takes about 10–20 minutes. Then open the run and click **Summary** to see what the digest would look like.

**Step 8. Go live.**
Run **Daily digest** once more with `dry_run` **unticked**. You get today's digest in Telegram. From now on it arrives by itself every day. GitHub often starts scheduled jobs hours late, though, so do step 9 to get it at 10:00 IST and to get instant replies to bot commands.

**Step 9 (recommended, about 10 minutes). On-time digest and instant replies with a free Cloudflare Worker.**
In this repository GitHub has been starting the 09:13 IST job 6–8 hours late, and running the 15-minute command check only every few hours. A small Cloudflare Worker (free, no card) starts the daily job on time and answers commands within seconds.

1. *Make a GitHub token for the Worker.* On GitHub click your profile picture → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**. Name it `scout-worker` and pick the longest expiry offered. Under **Repository access** choose **Only select repositories** and pick this repository. Under **Permissions** set **Actions** to *Read and write* and **Contents** to *Read-only*. Click **Generate token** and copy it (it starts with `github_pat_`).
2. *Create the Worker.* Sign up at <https://dash.cloudflare.com>. Open **Workers & Pages** → **Create** → start from the **Hello World** Worker, name it `opportunity-scout` and click **Deploy**. Then click **Edit code**, delete everything in the editor, paste the whole of [`worker/index.js`](worker/index.js) and click **Deploy**.
3. *Add the settings.* In the Worker open **Settings** → **Variables and Secrets** and add these (type **Secret**, except `GITHUB_REPO`, which is **Text**):

   | Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | the same bot token as in GitHub |
   | `TELEGRAM_CHAT_ID` | the same chat ID as in GitHub |
   | `GITHUB_TOKEN` | the token from part 1 |
   | `GITHUB_REPO` | this repository's `owner/name`, e.g. `victorsimba189-lab/Opportunity-Ai-agent.` |
   | `WEBHOOK_SECRET` | a made-up password of 20–40 letters and digits (no spaces) |

4. *Add the timer.* In **Settings** → **Triggers** (sometimes called **Trigger Events**) → **Cron Triggers**, add `45 3 * * *` and `15 5 * * *`. Cloudflare uses UTC, so these are 09:15 and 10:45 IST.
5. *Connect the bot.* Open `https://opportunity-scout.<your-subdomain>.workers.dev/setup?secret=<your WEBHOOK_SECRET>` in a browser (the Worker's address is shown at the top of its page). Every line should start with ✅, and your bot sends "Worker connected". Before the first daily run has finished, the "scan results" line shows ❌. That is expected.
6. *Try it.* Send `/status` to your bot. The reply arrives within a few seconds.

To go back to the GitHub-only setup, open the same `/setup` link with `&undo=1` added to the end. If you prefer the command line: `cd worker && npx wrangler deploy`, then `npx wrangler secret put <NAME>` for each secret (the cron triggers come from `worker/wrangler.toml`).

Optional: add `YOUTUBE_API_KEY` (YouTube Data API v3, free quota) for video results. You can also set **repository variables** (Settings → Secrets and variables → Actions → **Variables** tab) to override the defaults below.

### Free Gemini vs paid Claude

The free Gemini tier is the default and costs nothing. Two things to know:

- **Daily limits.** Each Gemini model has a free number of requests per minute and per day. Your own numbers are shown in Google AI Studio under **Usage / Rate limits**. The scout spaces its calls out (10 a minute) and, if a model's daily allowance runs out, it stops for the day, sends the digest with what it has, and checks the remaining pages the next day.
- **Your prompts may be used by Google** to improve its products on the free tier. The scout only sends public web pages and the profile in `config/profile.yaml`, which is already public in this repository.

Gemini **Pro** models have no free tier in the API, so the defaults are Flash models. To use Pro anyway, turn on billing in Google AI Studio and set the variable `SCOUT_ANALYSIS_MODEL` to `gemini-pro-latest`.

To switch to Claude (paid, about US$5–10 a month): create a key at <https://platform.claude.com>, add some credit, and save it as the secret `ANTHROPIC_API_KEY`. When that secret exists it is used instead of Gemini. To force one or the other, set the variable `SCOUT_LLM_PROVIDER` to `gemini` or `anthropic`.

### Configuration reference

| Name | Kind | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | secret | — | Gemini API, free tier (extraction + analysis) |
| `ANTHROPIC_API_KEY` | secret | unset | Optional: Claude instead of Gemini (paid) |
| `TAVILY_API_KEY` | secret | — | Web search |
| `TELEGRAM_BOT_TOKEN` | secret | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | secret | — | The only chat the bot will talk to |
| `YOUTUBE_API_KEY` | secret | unset | Optional YouTube search |
| `SCOUT_LLM_PROVIDER` | variable | `auto` | `gemini`, `anthropic`, or `auto` (Claude if its key is set, else Gemini) |
| `SCOUT_EXTRACT_MODEL` | variable | `gemini-3.5-flash-lite` / `claude-haiku-5-5` | Bulk page extraction |
| `SCOUT_ANALYSIS_MODEL` | variable | `gemini-3.6-flash` / `claude-opus-5-5` | Ranking, advice, startup analysis |
| `SCOUT_LLM_RPM` | variable | `10` (Gemini) / unlimited (Claude) | Maximum AI requests per minute |
| `SCOUT_MAX_LLM_USD_PER_MONTH` | variable | `12` | Stops Claude calls when reached (Gemini free tier is counted as $0) |
| `SCOUT_MAX_TAVILY_PER_DAY` | variable | `30` | Stops searches when reached |

The Cloudflare Worker has its own settings (step 9): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GITHUB_TOKEN`, `GITHUB_REPO` and `WEBHOOK_SECRET`, plus optional `GITHUB_BRANCH` (default `main`) and `DATA_BRANCH` (default `scout-data`).

---

## Schedule and timing

- GitHub cron runs in UTC, and IST is UTC+5:30 with no daylight saving, so 10:00 IST = 04:30 UTC.
- GitHub can start scheduled jobs late and drops many of them. In this repository the 09:13 IST job has started 6–8 hours late, and the 15-minute command check has run only every few hours.
- **With the Cloudflare Worker (step 9):** the Worker starts the job at **09:15 IST**, scouting takes 10–20 minutes, and the job **waits until 10:00 IST** before sending. If GitHub's API is briefly down, the Worker retries, and it tries again at **10:45 IST**. If it can't start the job at all (for example, an expired token), it tells you in Telegram. **Expect delivery at 10:00 IST.**
- **Without the Worker:** GitHub's own schedule starts the job (09:13 IST, fallback 10:43 IST), so the digest arrives whenever GitHub gets round to it, often in the afternoon.
- GitHub's schedule stays on as a backup alongside the Worker. A run that starts after the digest was sent exits within seconds.
- **Duplicate prevention:** only one digest per IST date. Each part's Telegram message ID is recorded as it is sent, so a retry after a partial failure sends only the missing parts. Items are announced once, and deadline reminders fire once per threshold (14/7/3/1 days).
- Public repos have their schedules disabled after 60 days without activity. The daily job re-enables both workflows on every scheduled run, and the Worker re-enables the daily workflow before starting it.
- **Commands with the Worker:** Telegram passes each command to the Worker, which replies within seconds using `replies.json`. Every daily run renders that file, so replies show the latest scan and end with the time it was saved. The Worker only accepts requests that carry `WEBHOOK_SECRET`, and it ignores every chat except `TELEGRAM_CHAT_ID`. While the Worker is connected, the GitHub poller sees the webhook and does nothing.
- **Commands without the Worker:** they are answered by polling, scheduled every 15 minutes but in practice whenever GitHub runs it. Updates are acknowledged only after the reply is sent. The bot ignores every chat except `TELEGRAM_CHAT_ID`.

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
| Cloudflare Worker (2 cron runs + a few commands a day; free plan allows 100,000 requests a day) | Free |
| Gemini free tier (default: ~60 extraction + 3 analysis calls/day) | Free |
| **Total with Gemini** | **US$0** |
| Optional instead: Claude Haiku 5.5 extraction + Opus 5.5 analysis | ≈ US$5–10 per month (hard cap: `SCOUT_MAX_LLM_USD_PER_MONTH`, default $12) |

`/status` shows Tavily credits and AI calls (and estimated Claude spend) for the month. Costs fall over time because URLs already processed are never re-extracted.

## Data and privacy

- The SQLite DB and `replies.json` (the rendered command replies) live on the `scout-data` branch (one commit, overwritten each run). A 30-day backup of the DB is attached to each daily run as an artifact.
- The Worker's GitHub token can only start workflows in, and read, this one repository. Its secrets stay in Cloudflare and are never sent anywhere except Telegram and GitHub.
- This repo is **public**, so the DB, workflow logs and `config/profile.yaml` are publicly readable. They contain only public listings and the digest text. Secrets and your chat ID are never stored or logged. Make the repo private if you prefer; that gives 2,000 free Actions minutes/month, which is enough if the poller runs every 30 minutes instead of 15.

## Customising

- **Profile:** edit `config/profile.yaml`. Set your nationality, preferred regions and the internship window.
- **Searches, official pages, feeds and weights:** edit `config/internships.yaml`, `config/programmes.yaml` and `config/startups.yaml`. Each Tavily query costs 1 credit per day.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                        # offline tests (no API keys needed)
export GEMINI_API_KEY=... TAVILY_API_KEY=...   # or ANTHROPIC_API_KEY=...
python -m scout scout internships       # run one agent, print new items
python -m scout daily --dry-run         # full run, print the digest instead of sending
python -m scout report internships      # print what /internships would reply
python -m scout replies                 # write data/replies.json (what the Worker serves)
cd worker && node --test                # Worker tests (Node 18+, no dependencies)
```

## Troubleshooting

- **No digest arrived.** Check **Actions → Daily digest** for today's runs and read the job summary. Missing secrets are listed in the job summary, and a scheduled run fails straight away if the Telegram ones are absent. If the schedule shows as disabled, click **Enable workflow**.
- **"Gemini daily free quota … is used up" in the summary.** Normal on busy days; the rest is checked tomorrow. To spread the load, lower `SCOUT_MAX_CANDIDATES` (default 25 pages per agent) or check your limits in Google AI Studio.
- **"API key not valid" or "free tier is not available in your country".** Recreate the key in AI Studio and update the `GEMINI_API_KEY` secret.
- **The bot doesn't answer commands.** Check that `TELEGRAM_CHAT_ID` matches the ID from `whoami`. With the Worker, open its `/setup?secret=…` link again: each ❌ line says what to fix, and the Worker's **Logs** tab in Cloudflare shows errors. Without the Worker, look at the latest **Telegram commands** run.
- **"could not start today's digest run" in Telegram.** The Worker's GitHub token was rejected, usually because it expired. Make a new one (step 9, part 1) and replace the `GITHUB_TOKEN` secret in the Worker.
- **`whoami` fails with "webhook is active".** While the Worker is connected, GitHub can't read the bot's messages. Remove `TELEGRAM_CHAT_ID` from the Worker and message the bot: it replies with your chat ID. Then add the ID back.
- **Too many or too few items.** Adjust the queries and weights in `config/*.yaml`, or `min_relevance` in the agent classes.
