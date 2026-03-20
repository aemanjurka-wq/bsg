"""
BSG Usinger Land — DBB Spielplan Scraper (Playwright)
======================================================
basketball-bund.net rendert alle Daten via JavaScript.
Deshalb brauchen wir einen echten Browser (Playwright).

Installation lokal:
    pip install playwright
    playwright install chromium

Lokal testen:
    python scrape_dbb.py

GitHub Actions: wird automatisch via .github/workflows/spielplan.yml ausgeführt
"""

import json
import re
import asyncio
from datetime import datetime, timezone
from playwright.async_api import async_playwright

TEAMS = {
    "Herren":      "314544",
    "Damen":       "314543",
    "MU18":        "314547",
    "WU18":        "316203",
    "MU16":        "314548",
    "MU14 Bezirk": "314549",
    "WU14 Bezirk": "316717",
    "MU14 Kreis":  "314550",
    "WU14 Kreis":  "316717",
    "Mix U12":     "314551",
    "WU12":        "316675",
    "WU10":        "322003",
    "Kreis A X10": "314552",
}

BSG_NAMEN = ["bsg usinger", "usinger land"]


def ist_bsg(name: str) -> bool:
    n = name.lower().strip()
    return any(s in n for s in BSG_NAMEN)


def parse_datum(text: str) -> str | None:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if not m:
        return None
    tag, monat, jahr = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(jahr) == 2:
        jahr = "20" + jahr
    return f"{int(jahr):04d}-{monat:02d}-{tag:02d}"


def parse_zeit(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def ergebnis_und_sieg(text: str):
    m = re.search(r"(\d+)\s*:\s*(\d+)", text)
    if not m:
        return None, None
    h, g = int(m.group(1)), int(m.group(2))
    return f"{h}:{g}", h > g


async def scrape_team(page, team_name: str, mid: str) -> list[dict]:
    url = f"https://www.basketball-bund.net/mannschaft/{mid}/spielplan"
    spiele = []

    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector("table", timeout=15000)
    except Exception as e:
        print(f"  ⚠ {team_name}: Seite nicht geladen — {e}")
        return spiele

    rows = await page.query_selector_all("table tr")

    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 3:
            continue

        texte = [((await c.inner_text()).strip()) for c in cells]

        # Datum finden
        datum = None
        zeit  = ""
        for t in texte:
            d = parse_datum(t)
            if d:
                datum = d
                zeit  = parse_zeit(t)
                break
        if not datum:
            continue

        # Team-Namen aus Links
        links      = await row.query_selector_all("a")
        teamnamen  = [(await l.inner_text()).strip() for l in links if (await l.inner_text()).strip()]
        heim = teamnamen[0] if len(teamnamen) > 0 else ""
        gast = teamnamen[1] if len(teamnamen) > 1 else ""

        # Fallback: direkt aus Zellen
        if not heim and len(texte) >= 4:
            for i, t in enumerate(texte):
                if parse_datum(t) and i + 2 < len(texte):
                    heim = texte[i + 1]
                    gast = texte[i + 2]
                    break

        if not heim or not gast:
            continue
        if not (ist_bsg(heim) or ist_bsg(gast)):
            continue

        # Ergebnis aus letzter Spalte
        ergebnis_str, heim_sieg = None, None
        for t in reversed(texte):
            e, s = ergebnis_und_sieg(t)
            if e:
                ergebnis_str, heim_sieg = e, s
                break

        spiele.append({
            "datum":    datum,
            "zeit":     zeit,
            "team":     team_name,
            "heim":     heim,
            "gast":     gast,
            "ergebnis": ergebnis_str,
            "heimSieg": heim_sieg,
        })

    # Duplikate entfernen
    gesehen, eindeutig = set(), []
    for s in spiele:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in gesehen:
            gesehen.add(key)
            eindeutig.append(s)

    print(f"  ✓ {team_name}: {len(eindeutig)} Spiele")
    return eindeutig


async def main():
    print("BSG Usinger Land — Spielplan Scraper")
    print("=" * 42)

    alle_spiele      = []
    bereits_gescrapt = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # Cookie-Banner einmalig wegklicken
        try:
            await page.goto("https://www.basketball-bund.net", timeout=20000)
            await page.wait_for_timeout(2000)
            for btn in await page.query_selector_all("button"):
                txt = (await btn.inner_text()).lower()
                if any(w in txt for w in ["akzeptiere", "zustimmen", "ich akzeptiere"]):
                    await btn.click()
                    print("  ✓ Cookie-Banner geschlossen")
                    break
        except Exception:
            pass

        for team_name, mid in TEAMS.items():
            if mid in bereits_gescrapt:
                print(f"  ↩ {team_name}: ID {mid} bereits abgerufen")
                continue
            bereits_gescrapt.add(mid)
            spiele = await scrape_team(page, team_name, mid)
            alle_spiele.extend(spiele)

        await browser.close()

    alle_spiele.sort(key=lambda s: (s["datum"], s.get("zeit", "")))

    ausgabe = {
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
        "anzahl":       len(alle_spiele),
        "spiele":       alle_spiele,
    }

    with open("spiele.json", "w", encoding="utf-8") as f:
        json.dump(ausgabe, f, ensure_ascii=False, indent=2)

    print("=" * 42)
    print(f"✓ {len(alle_spiele)} Spiele gespeichert → spiele.json")


if __name__ == "__main__":
    asyncio.run(main())
