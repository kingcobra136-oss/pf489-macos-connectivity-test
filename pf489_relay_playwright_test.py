#!/usr/bin/env python3
"""One-shot public-safe Playwright proxy test; no login, booking, cancellation, or LINE."""
import asyncio
import json
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

ORIGIN = "https://pf489-vercel-connectivity-test-github-usage.vercel.app"
ALLOWED_POSTS = {
    "kunitachi": "/kunitachi/web/Wg_ModeSelect.aspx",
    "kokubunji": "/Kokubunji/WebR/Home/WgR_ModeSelect",
}
RESULTS = {}


def allowed_post(city, parsed_path, post_data):
    if parsed_path.lower() != ALLOWED_POSTS[city].lower():
        return False
    values = parse_qs(post_data or "", keep_blank_values=True)
    if city == "kunitachi":
        return (
            values.get("dlSSCategory$ctl00$btnSSCategory") == ["スポーツ施設"]
            and bool(values.get("__VIEWSTATE"))
            and bool(values.get("__EVENTVALIDATION"))
            and not any(k.lower().startswith(("rbtnlogin", "rbtnmessage")) for k in values)
        )
    return (
        values.get("__EVENTTARGET") == ["ssCategory"]
        and values.get("__EVENTARGUMENT") == ["30"]
        and bool(values.get("__RequestVerificationToken"))
    )


async def test_city(browser, city):
    counts = {"get_ok": 0, "post_ok": 0, "post_blocked": 0, "unsafe_request_blocked": 0}
    context = await browser.new_context(
        extra_http_headers={"X-PF489-Test": "20261112-readonly"},
        service_workers="block",
    )
    page = await context.new_page()
    prefix = "/kunitachi/web/" if city == "kunitachi" else "/Kokubunji/WebR/"

    async def intercept(route):
        req = route.request
        p = urlparse(req.url)
        path = p.path
        in_scope = (
            p.scheme == "https"
            and p.netloc == urlparse(ORIGIN).netloc
            and path.lower().startswith(prefix.lower())
        )
        if not in_scope:
            counts["unsafe_request_blocked"] += 1
            await route.abort()
        elif req.method in ("GET", "HEAD"):
            await route.continue_()
        elif req.method == "POST" and allowed_post(city, path, req.post_data):
            await route.continue_()
        else:
            counts["post_blocked"] += 1
            await route.abort()

    async def response_log(response):
        req = response.request
        path = urlparse(response.url).path
        if response.status < 400:
            if req.method == "GET":
                counts["get_ok"] += 1
            if req.method == "POST" and path.lower() == ALLOWED_POSTS[city].lower():
                counts["post_ok"] += 1
        if req.method == "POST":
            print(json.dumps(
                {"city": city, "method": "POST", "status": response.status, "path": path},
                ensure_ascii=False,
            ), flush=True)

    await page.route("**/*", intercept)
    page.on("response", response_log)

    initial = ORIGIN + ("/kunitachi/web/" if city == "kunitachi" else "/Kokubunji/WebR/")
    try:
        response = await page.goto(initial, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else None
        if status != 200:
            raise RuntimeError(f"Entry did not reach HTTP 200: status={status}")
        if city == "kunitachi":
            selector = 'input[name="dlSSCategory$ctl00$btnSSCategory"]'
            clicked = False
            for _ in range(20):
                for frame in page.frames:
                    button = frame.locator(selector)
                    if await button.count():
                        await button.first.click(timeout=8000)
                        clicked = True
                        break
                if clicked:
                    break
                await page.wait_for_timeout(500)
            if not clicked:
                raise RuntimeError("Sports facility selection not found in frames")
        else:
            await page.locator("#category_30").click(timeout=15000)
        await page.wait_for_timeout(1800)
        if counts["post_ok"] < 1:
            raise RuntimeError("Allowed read-only selection POST did not receive successful HTTP response")
        if counts["post_blocked"] != 0:
            raise RuntimeError("Unexpected POST was blocked; route behavior needs investigation")
        print("CITY_PASS " + json.dumps({"city":city, "entry_status":status, **counts},
            ensure_ascii=False), flush=True)
        return True
    except Exception as exc:
        print("CITY_FAIL " + json.dumps({"city":city, "reason":str(exc)[:250], **counts},
            ensure_ascii=False), flush=True)
        return False
    finally:
        await context.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            passed = []
            for city in ("kunitachi", "kokubunji"):
                passed.append(await test_city(browser, city))
            print("PF489_RELAY_CONCLUSION " + json.dumps({"passed":all(passed),
                "city_results":passed}, ensure_ascii=False), flush=True)
            return 0 if all(passed) else 1
        finally:
            await browser.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
