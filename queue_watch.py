import os, re, asyncio, logging
import httpx
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TG_TOKEN     = os.environ["TG_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]
QUEUE_URL    = os.getenv("QUEUE_URL", "https://lounge.kantorimigrasingurahrai.com/ijin-tinggal")
TICKET       = os.getenv("TARGET_TICKET", "L066").upper()
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
HEADS_UP     = int(os.getenv("HEADS_UP", "5"))   # warn when this many tickets away

PREFIX, TARGET = re.match(r"([A-Z]+)0*(\d+)", TICKET).groups()
TARGET = int(TARGET)
TICKET_RE = re.compile(rf"\b{PREFIX}\s*-?\s*0*(\d{{1,4}})\b", re.I)

async def tg(text):
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"})
            return r.json().get("ok", False)
        except Exception as e:
            log.error(f"Telegram: {e}"); return False

async def read_numbers(page):
    await page.goto(QUEUE_URL, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(3)
    text = await page.inner_text("body")
    return sorted({int(n) for n in TICKET_RE.findall(text)})

async def main():
    log.info(f"Watching {QUEUE_URL} for {TICKET} every {POLL_SECONDS}s")
    await tg(f"👀 *Queue watch started*\nWaiting for *{TICKET}*\n{QUEUE_URL}")
    warned = False
    errors = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await browser.new_page()
        while True:
            try:
                nums = await read_numbers(page)
                errors = 0
                log.info(f"{PREFIX} numbers on page: {nums}")
                if TARGET in nums:
                    await tg(f"🔔 *{TICKET} IS BEING CALLED!*\nGo to the counter now.\n{QUEUE_URL}")
                    break
                current = max(nums) if nums else None
                if current is not None and current > TARGET:
                    await tg(f"⚠️ Queue is at *{PREFIX}{current:03d}* — past {TICKET}. Check the counter!")
                    break
                if current is not None and not warned and TARGET - current <= HEADS_UP:
                    await tg(f"⏳ Queue at *{PREFIX}{current:03d}* — {TARGET - current} away from {TICKET}. Get ready.")
                    warned = True
            except Exception as e:
                errors += 1
                log.error(f"Check failed: {e}")
                if errors == 5:
                    await tg(f"❌ *Queue watch* can't read the page (5 failures in a row)\n`{e}`")
            await asyncio.sleep(POLL_SECONDS)
        await browser.close()
    # Stay alive so Railway's restart policy doesn't start over and re-alert
    log.info(f"Done — alert for {TICKET} sent. Idling.")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
