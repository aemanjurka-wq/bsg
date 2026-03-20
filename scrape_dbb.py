"""
BSG Usinger Land — DBB Spielplan Scraper
=========================================
basketball-bund.net ist eine Angular SPA mit Hash-Routing.
Korrekte URL: https://www.basketball-bund.net/static/#/mannschaft/{id}/spielplan
(NICHT /mannschaft/{id}/spielplan — das ergibt "Seite nicht gefunden")
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

TEAMS = {
    "Herren":       "314544",
    "Damen":        "314543",
    "MU18":         "314547",
    "WU18":         "316203",
    "MU16":         "314548",
    "MU14 Bezirk":  "314549",
    "WU14 Bezirk":  "316717",
    "MU14 Kreis":   "314550",
    "Mix U12":      "314551",
    "WU12":         "316675",
    "WU10":         "322003",
    "Kreis A X10":  "314552",
}

# ← KORRIGIERTE URL mit Hash-Routing
BASE_URL = "https://www.basketball-bund.net/static/#/mannschaft/{id}/spielplan"

BSG_NAMEN = ["bsg usinger", "usinger land", "bsg u."]


def ist_bsg(name: str) -> bool:
    n = (name or "").lower().strip()
    return any(s in n for s in BSG_NAMEN)


def parse_datum(text: str) -> str | None:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text or "")
    if not m:
        return None
    tag, monat, jahr = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(jahr) == 2:
        jahr = "20" + jahr
    return f"{int(jahr):04d}-{monat:02d}-{tag:02d}"


def parse_zeit(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})\s*(?:Uhr)?", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def ergebnis_sieg(text: str):
    m = re.search(r"(\d+)\s*:\s*(\d+)", text or "")
    if not m:
        return None, None
    h, g = int(m.group(1)), int(m.group(2))
    return f"{h}:{g}", h > g


async def scrape_mannschaft(page, team_name: str, mid: str) -> list[dict]:
    url = BASE_URL.format(id=mid)
    spiele = []

    try:
        print(f"  → {team_name}: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Angular braucht Zeit zum Rendern — warten bis Tabelle erscheint
        try:
            await page.wait_for_selector("table tr td", timeout=20_000)
        except PWTimeout:
            # Debug: was sehen wir auf der Seite?
            text = (await page.inner_text("body") or "")[:200]
            print(f"  ⚠  {team_name}: Timeout — Seiteninhalt: {text!r}")
            return spiele

        rows = await page.query_selector_all("table tr")
        print(f"  [i] {team_name}: {len(rows)} Zeilen gefunden")

        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 3:
                continue

            texte = [((await c.inner_text()) or "").strip() for c in cells]
            zeile_text = " ".join(texte)

            datum = parse_datum(zeile_text)
            if not datum:
                continue

            if not any(s in zeile_text.lower() for s in BSG_NAMEN):
                continue

            zeit = parse_zeit(zeile_text)

            # Teamnamen aus Links
            links = await row.query_selector_all("a")
            namen = [(await l.inner_text() or "").strip() for l in links]
            namen = [n for n in namen if n and len(n) > 2 and not re.match(r"^\d", n)]

            heim = namen[0] if len(namen) > 0 else ""
            gast = namen[1] if len(namen) > 1 else ""

            # Fallback aus Zellen
            if not heim:
                for i, t in enumerate(texte):
                    if parse_datum(t) and i + 2 < len(texte):
                        heim = texte[i + 1]
                        gast = texte[i + 2]
                        break

            ergebnis, heim_sieg = None, None
            for t in reversed(texte):
                e, s = ergebnis_sieg(t)
                if e:
                    ergebnis, heim_sieg = e, s
                    break

            spiele.append({
                "datum":    datum,
                "zeit":     zeit,
                "team":     team_name,
                "heim":     heim or "BSG Usinger Land",
                "gast":     gast or "",
                "ergebnis": ergebnis,
                "heimSieg": heim_sieg,
            })

    except Exception as e:
        print(f"  ✗  {team_name}: {e}")

    # Duplikate entfernen
    seen, result = set(), []
    for s in spiele:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in seen:
            seen.add(key)
            result.append(s)

    print(f"  ✓  {team_name}: {len(result)} Spiele")
    return result


async def cookie_schliessen(page):
    try:
        await page.goto("https://www.basketball-bund.net/static/", wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(3_000)
        for btn in await page.query_selector_all("button, a"):
            txt = (await btn.inner_text() or "").lower()
            if any(w in txt for w in ["akzeptiere", "ich akzeptiere", "zustimmen", "accept all"]):
                await btn.click()
                await page.wait_for_timeout(1_000)
                print("  ✓  Cookie-Banner geschlossen")
                return
        print("  ℹ  Kein Cookie-Banner")
    except Exception as e:
        print(f"  ⚠  Cookie: {e}")


async def main():
    print("BSG Usinger Land — Spielplan Scraper")
    print("=" * 44)

    alle_spiele = []
    bereits_gescrapt = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        await cookie_schliessen(page)

        for team_name, mid in TEAMS.items():
            if mid in bereits_gescrapt:
                print(f"  ↩  {team_name}: übersprungen")
                continue
            bereits_gescrapt.add(mid)
            spiele = await scrape_mannschaft(page, team_name, mid)
            alle_spiele.extend(spiele)

        await browser.close()

    # Deduplizieren + sortieren
    seen, eindeutig = set(), []
    for s in alle_spiele:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in seen:
            seen.add(key)
            eindeutig.append(s)
    eindeutig.sort(key=lambda s: (s["datum"], s.get("zeit", "")))

    ausgabe = {
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
        "anzahl":       len(eindeutig),
        "spiele":       eindeutig,
    }

    with open("spiele.json", "w", encoding="utf-8") as f:
        json.dump(ausgabe, f, ensure_ascii=False, indent=2)

    print("=" * 44)
    print(f"✓  {len(eindeutig)} Spiele → spiele.json")


if __name__ == "__main__":
    asyncio.run(main())
