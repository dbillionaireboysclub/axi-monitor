import os, asyncio, logging
from datetime import datetime, timezone
import httpx
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VT_EMAIL    = os.environ["VT_EMAIL"]
VT_PASSWORD = os.environ["VT_PASSWORD"]
TG_TOKEN    = os.environ["TG_TOKEN"]
TG_CHAT_ID  = os.environ["TG_CHAT_ID"]
LOGIN_URL   = "https://go.vtaffiliates.com/v2/login/"
REPORT_URL  = "https://go.vtaffiliates.com/partner/reports/registration"

def parse_num(val):
    try: return float(str(val).replace(",","").replace("$","").replace(" ",""))
    except: return 0.0

def is_recent_month(date_str):
    try:
        today = datetime.now(timezone.utc)
        curr_month, curr_year = today.month, today.year
        if curr_month == 1:
            prev_month, prev_year = 12, curr_year - 1
        else:
            prev_month, prev_year = curr_month - 1, curr_year

        s = str(date_str).strip()
        # Strip timestamp — take date part only
        s = s.split(" ")[0].split("T")[0]

        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]:
            try:
                d = datetime.strptime(s, fmt)
                if d.month == curr_month and d.year == curr_year:
                    return True, "this month"
                if d.month == prev_month and d.year == prev_year:
                    return True, "last month"
                return False, None
            except: continue
        return False, None
    except: return False, None

async def tg(text):
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"})
            return r.json().get("ok", False)
        except Exception as e:
            log.error(f"Telegram: {e}"); return False

async def fetch_vt_data():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        page = await browser.new_page()
        try:
            log.info("Logging in to VT Markets...")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            await page.locator('#user').wait_for(timeout=10000)
            await page.locator('#user').fill(VT_EMAIL)
            await asyncio.sleep(0.5)
            await page.locator('#password').fill(VT_PASSWORD)
            await asyncio.sleep(0.5)
            await page.evaluate(f"""
                document.querySelector('#user').value = '{VT_EMAIL}';
                document.querySelector('#password').value = '{VT_PASSWORD}';
                sumbitForm();
            """)
            await asyncio.sleep(5)
            await page.wait_for_load_state("networkidle", timeout=20000)

            if "login" in page.url.lower() or "v2" in page.url.lower():
                raise RuntimeError(f"Login failed — still on: {page.url}")

            api_data = []

            async def intercept(response):
                if "processregreport" in response.url and response.status == 200:
                    try:
                        body = await response.json()
                        if "Registrations" in body and isinstance(body["Registrations"], list):
                            api_data.append(body["Registrations"])
                    except Exception as e:
                        log.debug(f"Intercept error: {e}")

            page.on("response", intercept)
            await page.goto(REPORT_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

            if not api_data:
                raise RuntimeError("No registration data captured.")

            raw = max(api_data, key=len)
            if raw:
                log.info(f"Column names: {list(raw[0].keys())}")
                log.info(f"Sample row: {raw[0]}")
            return raw
        finally:
            await browser.close()

def process(raw):
    today = datetime.now(timezone.utc)
    curr_label = today.strftime("%B %Y")
    if today.month == 1:
        prev_label = datetime(today.year - 1, 12, 1).strftime("%B %Y")
    else:
        prev_label = datetime(today.year, today.month - 1, 1).strftime("%B %Y")

    this_month, last_month = [], []

    for row in raw:
        reg_date = str(row.get("Registration_Date", "")).strip()
        valid, period = is_recent_month(reg_date)

        if not valid:
            continue

        deps  = parse_num(row.get("Net_Deposits", 0))
        withs = parse_num(row.get("Withdrawals", 0))
        first = parse_num(row.get("First_Deposit", 0))
        pct   = (withs / deps * 100) if deps > 0 else 0

        if pct >= 100:
            alert = "critical"
        elif pct >= 50:
            alert = "warning"
        else:
            alert = "none"

        entry = {
            "user_id": row.get("User_ID", "—"),
            "name":    row.get("Customer_Name", "—"),
            "country": str(row.get("Country", "—")).strip(),
            "reg":     reg_date.split(" ")[0],
            "deps":    deps,
            "withs":   withs,
            "first":   first,
            "pct":     pct,
            "alert":   alert,
            "period":  period,
        }

        if period == "this month":
            this_month.append(entry)
        else:
            last_month.append(entry)

    return this_month, last_month, curr_label, prev_label

async def scan():
    log.info("="*40 + " SCAN START")
    try:
        rows = await fetch_vt_data()
    except Exception as e:
        log.error(f"Fetch failed: {e}")
        await tg(f"❌ *VT Markets Monitor Error*\n\n`{e}`")
        return

    this_month, last_month, curr_label, prev_label = process(rows)
    all_members = this_month + last_month
    warnings  = [c for c in all_members if c["alert"] == "warning"]
    criticals = [c for c in all_members if c["alert"] == "critical"]

    log.info(f"This month: {len(this_month)} | Last month: {len(last_month)} | "
             f"Warnings: {len(warnings)} | Critical: {len(criticals)}")

    # Critical alerts — 100%+ withdrawn
    for c in criticals:
        await tg(
            f"🔴 *CRITICAL — FULL WITHDRAWAL*\n\n"
            f"👤 *{c['name']}*\n"
            f"🆔 `{c['user_id']}`\n"
            f"🌍 {c['country']}\n"
            f"📅 Registered: {c['reg']} _({c['period']})_\n\n"
            f"💰 Net Deposits: *${c['deps']:,.2f}*\n"
            f"📤 Withdrawn: *${c['withs']:,.2f}*\n\n"
            f"🔴 *{c['pct']:.1f}%* withdrawn — full exit"
        )
        await asyncio.sleep(0.5)

    # Warning alerts — 50–99% withdrawn
    for c in warnings:
        await tg(
            f"⚠️ *WARNING — HIGH WITHDRAWAL*\n\n"
            f"👤 *{c['name']}*\n"
            f"🆔 `{c['user_id']}`\n"
            f"🌍 {c['country']}\n"
            f"📅 Registered: {c['reg']} _({c['period']})_\n\n"
            f"💰 Net Deposits: *${c['deps']:,.2f}*\n"
            f"📤 Withdrawn: *${c['withs']:,.2f}*\n\n"
            f"⚠️ *{c['pct']:.1f}%* withdrawn — monitor closely"
        )
        await asyncio.sleep(0.5)

    # Daily summary
    curr_deps  = sum(c['deps']  for c in this_month)
    curr_withs = sum(c['withs'] for c in this_month)
    prev_deps  = sum(c['deps']  for c in last_month)
    prev_withs = sum(c['withs'] for c in last_month)
    status = "🟢 All clear." if not warnings and not criticals \
             else f"⚠️ {len(warnings)} warning · 🔴 {len(criticals)} critical"

    await tg(
        f"📋 *VT Markets — Daily Report*\n\n"
        f"📅 *{curr_label}*\n"
        f"👥 Members: *{len(this_month)}*\n"
        f"💰 Total deposited: *${curr_deps:,.2f}*\n"
        f"📤 Total withdrawn: *${curr_withs:,.2f}*\n\n"
        f"📅 *{prev_label}*\n"
        f"👥 Members: *{len(last_month)}*\n"
        f"💰 Total deposited: *${prev_deps:,.2f}*\n"
        f"📤 Total withdrawn: *${prev_withs:,.2f}*\n\n"
        f"{status}"
    )

    log.info("Scan complete.")

async def check_manual_trigger():
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(url, params={"timeout": 0})
            updates = r.json().get("result", [])
            for update in updates:
                msg = update.get("message", {})
                text = msg.get("text", "")
                from_id = str(msg.get("chat", {}).get("id", ""))
                if text == "/scan" and from_id == str(TG_CHAT_ID):
                    await c.get(url, params={"offset": update["update_id"] + 1})
                    return True
        except Exception as e:
            log.error(f"Manual trigger check error: {e}")
    return False

async def main():
    H = int(os.getenv("CHECK_HOUR", "9"))
    M = int(os.getenv("CHECK_MINUTE", "0"))
    log.info(f"VT Markets Monitor started. Daily scan at {H:02d}:{M:02d} UTC")
    await tg(
        f"🟢 *VT Markets Monitor Online*\n"
        f"Daily scan at {H:02d}:{M:02d} UTC\n\n"
        f"⚠️ Alert at: 50%+ withdrawn\n"
        f"🔴 Critical at: 100%+ withdrawn\n"
        f"Monitoring: current + previous month\n\n"
        f"Send /scan anytime to trigger manually."
    )
    while True:
        now = datetime.now(timezone.utc)

        if now.hour == H and now.minute == M:
            await scan()
            await asyncio.sleep(61)
            continue

        if await check_manual_trigger():
            await tg("🔄 *Manual scan triggered...*")
            await scan()
            await asyncio.sleep(5)
            continue

        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
