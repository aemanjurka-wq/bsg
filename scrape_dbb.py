"""
BSG Usinger Land — DBB Spielplan Scraper (Playwright + iCal-Button)
====================================================================
basketball-bund.net rendert per JavaScript. Der iCal-Link wird erst
nach Laden der Seite sichtbar. Dieses Script:
  1. Öffnet jede Mannschaftsseite mit Playwright (headless Chromium)
  2. Wartet bis der Spielplan geladen ist
  3. Liest die Spielplandaten direkt aus der gerenderten Tabelle
  4. Speichert alles als spiele.json

Installation:
  pip install playwright icalendar
  playwright install chromium --with-deps

Lokal testen:
  python scrape_dbb.py
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ── Konfiguration ─────────────────────────────────────────────────────
TEAMS = {
    "Herren":       "314544",
    "Damen":        "314543",
    "MU18":         "314547",
    "WU18":         "316203",
    "MU16":         "314548",
    "MU14 Bezirk":  "314549",
    "WU14 Bezirk":  "316717",
    "MU14 Kreis":   "314550",
    "WU14 Kreis":   "316717",
    "Mix U12":      "314551",
    "WU12":         "316675",
    "WU10":         "322003",
    "Kreis A X10":  "314552",
}

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


# ── Spielplan einer Mannschaft scrapen ───────────────────────────────

async def scrape_mannschaft(page, team_name: str, mid: str) -> list[dict]:
    url = f"https://www.basketball-bund.net/mannschaft/{mid}/spielplan"
    spiele = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Warten bis entweder eine Tabelle oder der Text "keine Spiele" erscheint
        try:
            await page.wait_for_selector("table tr, .no-games, [class*='spielplan']", timeout=20_000)
        except PWTimeout:
            print(f"  ⚠  {team_name}: Timeout beim Laden")
            return spiele

        # Alle Tabellenzeilen sammeln
        rows = await page.query_selector_all("table tr")

        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 3:
                continue

            texte = []
            for cell in cells:
                texte.append(((await cell.inner_text()) or "").strip())

            # Datum finden
            datum = zeit = None
            for t in texte:
                d = parse_datum(t)
                if d:
                    datum = d
                    zeit  = parse_zeit(t)
                    break
            if not datum:
                continue

            # Teamnamen aus Links (zuverlässiger als Text-Parsing)
            links = await row.query_selector_all("a")
            namen = []
            for lnk in links:
                n = ((await lnk.inner_text()) or "").strip()
                if n and len(n) > 2 and not re.match(r"^\d", n):
                    namen.append(n)

            heim = namen[0] if len(namen) > 0 else ""
            gast = namen[1] if len(namen) > 1 else ""

            # Fallback: Zellen direkt lesen
            if not heim:
                for i, t in enumerate(texte):
                    if parse_datum(t) and i + 2 < len(texte):
                        heim = texte[i + 1]
                        gast = texte[i + 2]
                        break

            # Spiel muss BSG betreffen
            if not (ist_bsg(heim) or ist_bsg(gast)):
                # Nochmal im Rohtext der Zeile prüfen
                zeile_text = (await row.inner_text() or "").lower()
                if not any(s in zeile_text for s in BSG_NAMEN):
                    continue

            # Ergebnis: in letzter Zelle oder vorletzter
            ergebnis, heim_sieg = None, None
            for t in reversed(texte):
                e, s = ergebnis_sieg(t)
                if e:
                    ergebnis  = e
                    heim_sieg = s
                    break

            spiele.append({
                "datum":    datum,
                "zeit":     zeit or "",
                "team":     team_name,
                "heim":     heim or "BSG Usinger Land",
                "gast":     gast or "",
                "ergebnis": ergebnis,
                "heimSieg": heim_sieg,
            })

    except Exception as e:
        print(f"  ✗  {team_name}: {e}")

    # Duplikate weg
    seen, result = set(), []
    for s in spiele:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in seen:
            seen.add(key)
            result.append(s)

    print(f"  ✓  {team_name}: {len(result)} Spiele")
    return result


# ── Cookie-Banner wegklicken ─────────────────────────────────────────

async def cookie_banner_schliessen(page):
    try:
        await page.goto("https://www.basketball-bund.net", wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(2_000)
        for btn in await page.query_selector_all("button"):
            txt = ((await btn.inner_text()) or "").lower()
            if any(w in txt for w in ["akzeptiere", "zustimmen", "ich akzeptiere", "alle akzeptieren"]):
                await btn.click()
                await page.wait_for_timeout(500)
                print("  ✓  Cookie-Banner geschlossen")
                return
    except Exception:
        pass


# ── Hauptprogramm ────────────────────────────────────────────────────

async def main():
    print("BSG Usinger Land — Spielplan Scraper")
    print("=" * 44)

    alle_spiele      = []
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
        )
        page = await context.new_page()

        await cookie_banner_schliessen(page)

        for team_name, mid in TEAMS.items():
            if mid in bereits_gescrapt:
                print(f"  ↩  {team_name}: ID {mid} übersprungen")
                continue
            bereits_gescrapt.add(mid)
            spiele = await scrape_mannschaft(page, team_name, mid)
            alle_spiele.extend(spiele)

        await browser.close()

    # Global deduplizieren und sortieren
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
    if len(eindeutig) == 0:
        print("\n  Mögliche Ursachen für 0 Spiele:")
        print("  • Saison noch nicht gestartet / keine Spiele eingetragen")
        print("  • basketball-bund.net hat die Seitenstruktur geändert")
        print("  • Cookie-Banner hat Tabelle blockiert")


if __name__ == "__main__":
    asyncio.run(main())
