# Axi Withdrawal Monitor 🤖

Automatically logs into your Axi Affiliates dashboard, pulls the registration
report, and sends a Telegram alert for every Group 111 client who has withdrawn
more than 50% of their capital. Runs daily on Railway.

---

## Files

```
axi-monitor/
├── bot.py            # Main script (login → download → analyze → alert)
├── requirements.txt  # Python dependencies
├── Dockerfile        # Railway build config
├── railway.toml      # Railway deploy config
└── .env.example      # Environment variables reference
```

---

## Deploy to Railway — Step by Step

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "init axi monitor"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/axi-monitor.git
git push -u origin main
```

### 2. Create Railway project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Choose **Deploy from GitHub repo**
3. Select your `axi-monitor` repo
4. Railway will detect the Dockerfile automatically

### 3. Set Environment Variables

In Railway → your service → **Variables** tab, add:

| Variable | Value |
|---|---|
| `AXI_EMAIL` | Your Axi affiliate login email |
| `AXI_PASSWORD` | Your Axi affiliate password |
| `TG_TOKEN` | Telegram bot token (from @BotFather) |
| `TG_CHAT_ID` | Your group/channel chat ID |
| `CHECK_HOUR` | `9` (UTC hour — adjust to your timezone) |
| `WITHDRAWAL_THRESHOLD` | `0.50` (50%) |

### 4. Deploy

Click **Deploy** — Railway builds the Docker image and starts the bot.

You'll get a Telegram message: `🟢 Axi Monitor Online`

---

## How It Works

```
Every day at CHECK_HOUR (UTC)
  │
  ├─ Playwright launches headless Chromium
  ├─ Logs into records.axiaffiliates.com
  ├─ Opens /partner/reports/registration
  ├─ Downloads CSV (clicks Export button or intercepts network)
  ├─ Parses: Deposits, Withdrawals, UserID, Name, Country, etc.
  ├─ Flags: Withdrawals / Deposits > 50%
  ├─ Sends individual Telegram alert per flagged client
  └─ Sends daily summary message
```

---

## Adjusting the Scan Time

`CHECK_HOUR` is in **UTC**. Examples:

| Your timezone | UTC offset | To run at 9am local, set CHECK_HOUR to |
|---|---|---|
| Morocco (WET) | UTC+0/+1 | `9` or `8` |
| Dubai (GST) | UTC+4 | `5` |
| London (BST) | UTC+1 | `8` |

---

## Troubleshooting

**Bot says "Login failed"**
→ Double-check `AXI_EMAIL` and `AXI_PASSWORD` in Railway Variables

**Bot says "Could not find CSV data"**
→ Axi may have updated their dashboard UI. Open `bot.py`, find the
  `export_selectors` list, and update the button selector to match
  the current export button on the Axi site.

**Telegram alerts not sending**
→ Make sure your bot is added to the group/channel and `TG_CHAT_ID` is correct.
  For channels use the format `-100XXXXXXXXXX`.

---

## View Logs on Railway

Railway → your service → **Logs** tab — shows every scan in real time.
