import os
import io
import csv
import asyncio
import logging
from datetime import datetime

import httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Config (from environment variables) ───────────────────────────────────────
AXI_EMAIL    = os.environ["AXI_EMAIL"]
AXI_PASSWORD = os.environ["AXI_PASSWORD"]
TG_TOKEN     = os.environ["TG_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]

THRESHOLD    = float(os.getenv("WITHDRAWAL_THRESHOLD", "0.50"))   # 50%
REPORT_URL   = "https://records.axiaffiliates.com/partner/reports/registration"
LOGIN_URL    = "https://records.axiaffiliates.com/v2/login/"


# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_num(val: str) -> float:
    try:
        return float(str(val).replace(",", "").replace("$", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0.0


def withdrawal_pct(deposits: float, withdrawn: float) -> float:
    if deposits <= 0:
        return 0.0
    return withdrawn / deposits


def find_col(headers: list[str], *candidates: str) -> str | None:
    """Case-insensitive column finder."""
    norm = {h.lower().replace(" ", "").replace("_", ""): h for h in headers}
    for c in candidates:
        key = c.lower().replace(" ", "").replace("_", "")
        if key in norm:
            return norm[key]
    return None


# ── Telegram ──────────────────────────────────────────────────────────────────
async def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(url, json=payload)
            return r.json().get("ok", False)
        except Exception as e:
            log.error(f"Telegram error: {e}")
            return False


async def alert_flagged(client: dict) -> None:
    pct = withdrawal_pct(client["deposits"], client["withdrawn"]) * 100
    text = (
        f"⚠️ *GROUP 111 — WITHDRAWAL ALERT*\n\n"
        f"👤 *{client['name']}*\n"
        f"🆔 User ID: `{client['user_id']}`\n"
        f"🌍 Country: {client['country']}\n"
        f"📅 Registered: {client['reg_date']}\n\n"
        f"💰 Total Deposits: *${client['deposits']:,.2f}*\n"
        f"📤 Withdrawals:    *${client['withdrawn']:,.2f}*\n"
        f"📊 Net Deposits:   *${client['net_deposits']:,.2f}*\n\n"
        f"🔴 Withdrew *{pct:.1f}%* of capital — exceeds {THRESHOLD*100:.0f}% threshold\n"
        f"🕐 Detected: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    ok = await send_telegram(text)
    status = "✓ sent" if ok else "✗ failed"
    log.info(f"Telegram alert {status} → {client['name']} ({client['user_id']})")


async def send_summary(total: int, flagged: int) -> None:
    if flagged == 0:
        text = (
            f"✅ *Axi Daily Scan — All Clear*\n\n"
            f"Checked {total} clients · 0 violations\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    else:
        text = (
            f"📋 *Axi Daily Scan Complete*\n\n"
            f"Checked: {total} clients\n"
            f"Flagged: {flagged} (>{THRESHOLD*100:.0f}% withdrawal)\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    await send_telegram(text)


# ── Axi Scraper ───────────────────────────────────────────────────────────────
async def fetch_axi_csv() -> list[dict]:
    """
    Logs into Axi Affiliates, navigates to the registration report,
    downloads the CSV, and returns parsed rows.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            # ── 1. Login ──────────────────────────────────────────────────────
            log.info("Navigating to login page...")
            await page.goto(LOGIN_URL    = "https://records.axiaffiliates.com/v2/login/", timeout=30_000)

            log.info("Filling credentials...")
            await page.fill('input[type="email"], input[name="email"], input[name="login"], input[placeholder*="email" i], input[placeholder*="Email"], #email, #login', AXI_EMAIL)
            await page.fill('input[name="pass"], #password', AXI_PASSWORD)
            await page.click('button[type="submit"], input[type="submit"], button:has-text("Login"), button:has-text("Sign in")')
            await page.wait_for_load_state("networkidle", timeout=20_000)

            # Check login succeeded
            if "login" in page.url.lower():
                raise RuntimeError("Login failed — check AXI_EMAIL / AXI_PASSWORD")
            log.info(f"Logged in. Current URL: {page.url}")

            # ── 2. Navigate to report ─────────────────────────────────────────
            log.info("Opening registration report...")
            await page.goto(REPORT_URL, wait_until="networkidle", timeout=30_000)

            # ── 3. Download CSV ───────────────────────────────────────────────
            log.info("Waiting for export button...")
            # Axi typically has a CSV/Excel export button — try common selectors
            export_selectors = [
                'button:has-text("Export")',
                'button:has-text("CSV")',
                'a:has-text("Export")',
                'a:has-text("CSV")',
                '[data-export]',
                '.export-btn',
                '#export-csv',
            ]

            csv_text = None

            # Strategy A: click export button and intercept download
            for sel in export_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        log.info(f"Found export button: {sel}")
                        async with page.expect_download(timeout=20_000) as dl_info:
                            await btn.click()
                        download = await dl_info.value
                        stream = await download.path()
                        with open(stream, "r", encoding="utf-8-sig") as f:
                            csv_text = f.read()
                        log.info(f"Downloaded: {download.suggested_filename}")
                        break
                except PWTimeout:
                    continue
                except Exception as e:
                    log.debug(f"Selector {sel} failed: {e}")
                    continue

            # Strategy B: intercept XHR/fetch responses that look like CSV
            if not csv_text:
                log.info("No download button found — intercepting network response...")
                csv_responses = []

                async def capture_response(response):
                    ct = response.headers.get("content-type", "")
                    if "csv" in ct or "text/plain" in ct or "octet-stream" in ct:
                        try:
                            body = await response.body()
                            csv_responses.append(body.decode("utf-8-sig", errors="replace"))
                        except Exception:
                            pass

                page.on("response", capture_response)

                # Reload the page to trigger data fetch
                await page.reload(wait_until="networkidle", timeout=30_000)
                await asyncio.sleep(3)

                if csv_responses:
                    csv_text = max(csv_responses, key=len)  # take largest CSV response
                    log.info(f"Captured CSV via network intercept ({len(csv_text)} bytes)")

            if not csv_text:
                raise RuntimeError(
                    "Could not find CSV data. "
                    "The Axi dashboard may have changed — please update the selectors in bot.py."
                )

            # ── 4. Parse CSV ──────────────────────────────────────────────────
            rows = list(csv.DictReader(io.StringIO(csv_text)))
            log.info(f"Parsed {len(rows)} rows from CSV")
            return rows

        finally:
            await browser.close()


# ── Process rows ──────────────────────────────────────────────────────────────
def process_rows(raw_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Normalize rows and return (all_clients, flagged_clients)."""
    all_clients = []

    for row in raw_rows:
        headers = list(row.keys())

        dep_col  = find_col(headers, "Deposits", "First Deposit", "FirstDeposit")
        with_col = find_col(headers, "Withdrawals", "Withdrawal")
        net_col  = find_col(headers, "Net Deposits", "NetDeposits")
        id_col   = find_col(headers, "UserID", "User ID", "USERID", "Additional UserID", "AdditionalUserID")
        name_col = find_col(headers, "Customer Name", "CustomerName", "Name")
        reg_col  = find_col(headers, "Registration Date", "RegistrationDate")
        cty_col  = find_col(headers, "Country")

        deposits  = parse_num(row.get(dep_col, 0))
        withdrawn = parse_num(row.get(with_col, 0))
        net       = parse_num(row.get(net_col, 0))
        pct       = withdrawal_pct(deposits, withdrawn)

        client = {
            "user_id":     row.get(id_col, "—") if id_col else "—",
            "name":        row.get(name_col, "—") if name_col else "—",
            "country":     row.get(cty_col, "—") if cty_col else "—",
            "reg_date":    row.get(reg_col, "—") if reg_col else "—",
            "deposits":    deposits,
            "withdrawn":   withdrawn,
            "net_deposits": net,
            "pct":         pct,
            "flagged":     pct > THRESHOLD,
        }
        all_clients.append(client)

    flagged = [c for c in all_clients if c["flagged"]]
    return all_clients, flagged


# ── Main scan ─────────────────────────────────────────────────────────────────
async def run_scan() -> None:
    log.info("=" * 50)
    log.info("Starting daily Axi withdrawal scan...")

    try:
        raw_rows = await fetch_axi_csv()
    except Exception as e:
        log.error(f"Failed to fetch data: {e}")
        await send_telegram(f"❌ *Axi Monitor Error*\n\nFailed to fetch report:\n`{e}`")
        return

    all_clients, flagged = process_rows(raw_rows)
    log.info(f"Total clients: {len(all_clients)} | Flagged: {len(flagged)}")

    # Send individual alerts
    for client in flagged:
        await alert_flagged(client)
        await asyncio.sleep(0.5)  # avoid Telegram rate limit

    # Send daily summary
    await send_summary(len(all_clients), len(flagged))
    log.info("Scan complete.")


# ── Scheduler ─────────────────────────────────────────────────────────────────
async def main() -> None:
    CHECK_HOUR   = int(os.getenv("CHECK_HOUR", "9"))    # 09:00 by default
    CHECK_MINUTE = int(os.getenv("CHECK_MINUTE", "0"))

    log.info(f"Axi Monitor started. Daily scan at {CHECK_HOUR:02d}:{CHECK_MINUTE:02d} UTC")
    await send_telegram(
        f"🟢 *Axi Monitor Online*\n"
        f"Daily scan scheduled at {CHECK_HOUR:02d}:{CHECK_MINUTE:02d} UTC\n"
        f"Threshold: >{THRESHOLD*100:.0f}% withdrawal"
    )

    while True:
        now = datetime.utcnow()
        if now.hour == CHECK_HOUR and now.minute == CHECK_MINUTE:
            await run_scan()
            await asyncio.sleep(61)  # prevent double-trigger within same minute
        else:
            await asyncio.sleep(30)  # check every 30s


if __name__ == "__main__":
    asyncio.run(main())
