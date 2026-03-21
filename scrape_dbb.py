"""
BSG Usinger Land — DBB Spielplan Scraper
=========================================
basketball-bund.net = Angular SPA mit Hash-Routing.

Problem bisher:
  page.goto('https://...static/#/mannschaft/314544/spielplan')
  → Angular bootet, aber Router ignoriert den Hash beim ersten Load
  → Seite bleibt auf LIGENAUSWAHL hängen

Lösung:
  1. Basis-URL laden → Angular vollständig booten
  2. window.location.hash setzen → Angular Router navigiert
  3. Auf Tabelleninhalt warten
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

BASE = "https://www.basketball-bund.net/static/"
BSG  = ["bsg usinger", "usinger land"]


def ist_bsg(name):
    n = (name or "").lower()
    return any(s in n for s in BSG)

def parse_datum(text):
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text or "")
    if not m:
        return None
    t, mo, j = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(j) == 2:
        j = "20" + j
    return f"{int(j):04d}-{mo:02d}-{t:02d}"

def parse_zeit(text):
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""

def ergebnis_sieg(text):
    m = re.search(r"(\d+)\s*:\s*(\d+)", text or "")
    if not m:
        return None, None
    h, g = int(m.group(1)), int(m.group(2))
    return f"{h}:{g}", h > g


async def navigate_hash(page, mid):
    """Angular Router via window.location.hash ansprechen"""
    hash_path = f"#/mannschaft/{mid}/spielplan"

    # Hash setzen → Angular Router reagiert
    await page.evaluate(f"window.location.hash = '{hash_path}'")

    # Warten bis Tabelle mit Spielen erscheint
    try:
        await page.wait_for_selector("table tr td", timeout=25_000)
        return True
    except PWTimeout:
        body = (await page.inner_text("body") or "")[:200].replace("\n", " | ")
        print(f"    [timeout] {hash_path}")
        print(f"    [body]    {body!r}")
        return False


async def scrape_mannschaft(page, team_name, mid):
    spiele = []
    print(f"  → {team_name} (ID: {mid})")

    ok = await navigate_hash(page, mid)
    if not ok:
        print(f"  ⚠  {team_name}: kein Inhalt")
        return spiele

    rows = await page.query_selector_all("table tr")
    print(f"  [i] {len(rows)} Zeilen")

    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 3:
            continue

        texte = [((await c.inner_text()) or "").strip() for c in cells]
        zeile = " ".join(texte)

        datum = parse_datum(zeile)
        if not datum:
            continue
        if not any(s in zeile.lower() for s in BSG):
            continue

        zeit = parse_zeit(zeile)

        links = await row.query_selector_all("a")
        namen = [(await l.inner_text() or "").strip() for l in links]
        namen = [n for n in namen if n and len(n) > 2 and not re.match(r"^\d", n)]
        heim = namen[0] if namen else ""
        gast = namen[1] if len(namen) > 1 else ""

        if not heim:
            for i, t in enumerate(texte):
                if parse_datum(t) and i + 2 < len(texte):
                    heim, gast = texte[i+1], texte[i+2]
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

    seen, result = set(), []
    for s in spiele:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in seen:
            seen.add(key)
            result.append(s)

    print(f"  ✓  {team_name}: {len(result)} Spiele")
    return result


async def main():
    print("BSG Usinger Land — Spielplan Scraper")
    print("=" * 44)

    alle_spiele = []
    bereits = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()

        # Schritt 1: Basis laden → Angular vollständig booten
        print("  → Angular booten...")
        await page.goto(BASE, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(3_000)

        # Cookie-Banner schließen
        for btn in await page.query_selector_all("button, a"):
            txt = (await btn.inner_text() or "").lower()
            if any(w in txt for w in ["akzeptiere", "ich akzeptiere", "zustimmen", "accept all"]):
                await btn.click()
                await page.wait_for_timeout(1_000)
                print("  ✓  Cookie-Banner geschlossen")
                break

        # Kurz warten damit Angular fertig initialisiert ist
        await page.wait_for_timeout(2_000)

        # Schritt 2: Alle Teams via Hash-Navigation scrapen
        for team_name, mid in TEAMS.items():
            if mid in bereits:
                print(f"  ↩  {team_name}: übersprungen")
                continue
            bereits.add(mid)
            spiele = await scrape_mannschaft(page, team_name, mid)
            alle_spiele.extend(spiele)
            # Kurze Pause zwischen Teams
            await page.wait_for_timeout(500)

        await browser.close()

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
