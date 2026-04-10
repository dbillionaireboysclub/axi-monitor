import os, io, csv, asyncio, logging
from datetime import datetime, timezone
import httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

AXI_EMAIL    = os.environ["AXI_EMAIL"]
AXI_PASSWORD = os.environ["AXI_PASSWORD"]
TG_TOKEN     = os.environ["TG_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]
THRESHOLD    = float(os.getenv("WITHDRAWAL_THRESHOLD", "0.50"))
LOGIN_URL    = "https://records.axiaffiliates.com/v2/login/"
REPORT_URL   = "https://records.axiaffiliates.com/partner/reports/registration"

def parse_num(val):
    try: return float(str(val).replace(",","").replace("$","").replace(" ",""))
    except: return 0.0

def wpct(dep, with_): return (with_/dep) if dep > 0 else 0.0

def find_col(headers, *candidates):
    norm = {h.lower().replace(" ","").replace("_",""): h for h in headers}
    for c in candidates:
        k = c.lower().replace(" ","").replace("_","")
        if k in norm: return norm[k]
    return None

async def tg(text):
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"})
            return r.json().get("ok", False)
        except Exception as e:
            log.error(f"Telegram: {e}"); return False

async def fetch_axi_csv():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        page = await browser.new_page()
        try:
            log.info("Going to login page...")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            log.info("Filling login form...")
            await page.locator('#user').wait_for(timeout=10000)
            await page.locator('#user').fill(AXI_EMAIL)
            await asyncio.sleep(0.5)
            await page.locator('#password').fill(AXI_PASSWORD)
            await asyncio.sleep(0.5)

            log.info("Submitting via JS...")
            await page.evaluate(f"""
                document.querySelector('#user').value = '{AXI_EMAIL}';
                document.querySelector('#password').value = '{AXI_PASSWORD}';
                sumbitForm();
            """)

            await asyncio.sleep(5)
            await page.wait_for_load_state("networkidle", timeout=20000)

            log.info(f"After submit URL: {page.url}")
            if "login" in page.url.lower() or "v2" in page.url.lower():
                raise RuntimeError(f"Login failed — still on: {page.url}")
            log.info(f"Logged in. URL: {page.url}")

            log.info("Going to report...")
            await page.goto(REPORT_URL, wait_until="networkidle", timeout=30000)

            csv_text = None
            for sel in ['button:has-text("Export")','button:has-text("CSV")','a:has-text("Export")','a:has-text("CSV")','[data-export]','.export-btn']:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        async with page.expect_download(timeout=20000) as dl:
                            await btn.click()
                        d = await dl.value
                        with open(await d.path(), "r", encoding="utf-8-sig") as f:
                            csv_text = f.read()
                        log.info(f"Downloaded: {d.suggested_filename}"); break
                except: continue

            if not csv_text:
                resps = []
                async def cap(r):
                    ct = r.headers.get("content-type","")
                    if any(x in ct for x in ["csv","text/plain","octet"]):
                        try: resps.append((await r.body()).decode("utf-8-sig","replace"))
                        except: pass
                page.on("response", cap)
                await page.reload(wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)
                if resps: csv_text = max(resps, key=len)

            if not csv_text:
                raise RuntimeError("Could not find CSV export on the report page.")

            rows = list(csv.DictReader(io.StringIO(csv_text)))
            log.info(f"Parsed {len(rows)} rows"); return rows
        finally:
            await browser.close()

def process(raw):
    out = []
    for row in raw:
        h = list(row.keys())
        deps  = parse_num(row.get(find_col(h,"Deposits","First Deposit") or "", 0))
        withs = parse_num(row.get(find_col(h,"Withdrawals","Withdrawal") or "", 0))
        net   = parse_num(row.get(find_col(h,"Net Deposits","NetDeposits") or "", 0))
        pct   = wpct(deps, withs)
        out.append({
            "user_id": row.get(find_col(h,"UserID","User ID","USERID","Additional UserID") or "", "—"),
            "name":    row.get(find_col(h,"Customer Name","CustomerName","Name") or "", "—"),
            "country": row.get(find_col(h,"Country") or "", "—"),
            "reg":     row.get(find_col(h,"Registration Date","RegistrationDate") or "", "—"),
            "deps": deps, "withs": withs, "net": net, "pct": pct,
            "flagged": pct > THRESHOLD,
        })
    return out, [c for c in out if c["flagged"]]

async def scan():
    log.info("="*40 + " SCAN START")
    try: rows = await fetch_axi_csv()
    except Exception as e:
        log.error(f"Fetch failed: {e}")
        await tg(f"❌ *Axi Monitor Error*\n\n`{e}`"); return
    all_c, flagged = process(rows)
    log.info(f"Total: {len(all_c)} | Flagged: {len(flagged)}")
    for c in flagged:
        p = c['pct']*100
        await tg(
            f"⚠️ *GROUP 111 — WITHDRAWAL ALERT*\n\n"
            f"👤 *{c['name']}*\n🆔 `{c['user_id']}`\n🌍 {c['country']}\n📅 {c['reg']}\n\n"
            f"💰 Deposits: *${c['deps']:,.2f}*\n📤 Withdrawn: *${c['withs']:,.2f}*\n\n"
            f"🔴 *{p:.1f}%* of capital withdrawn"
        )
        await asyncio.sleep(0.5)
    msg = (f"✅ *Axi Scan — All Clear*\n{len(all_c)} clients checked" if not flagged
           else f"📋 *Axi Scan Done*\n{len(all_c)} checked · {len(flagged)} flagged")
    await tg(msg)
    log.info("Scan complete.")

async def main():
    H = int(os.getenv("CHECK_HOUR","9"))
    M = int(os.getenv("CHECK_MINUTE","0"))
    log.info(f"Axi Monitor started. Daily scan at {H:02d}:{M:02d} UTC")
    await tg(f"🟢 *Axi Monitor Online*\nScan at {H:02d}:{M:02d} UTC · Threshold >{THRESHOLD*100:.0f}%")
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == H and now.minute == M:
            await scan(); await asyncio.sleep(61)
        else:
            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
