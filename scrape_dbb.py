"""
BSG Usinger Land — DBB Scraper mit Debug-Modus
================================================
Dieses Script:
1. Öffnet basketball-bund.net/mannschaft/314544/spielplan
2. Wartet 8 Sekunden auf JS-Rendering
3. Speichert den gerenderten HTML als debug_herren.html
4. Macht einen Screenshot als debug_herren.png
5. Versucht Spielplan-Daten zu lesen und speichert spiele.json

So können wir exakt sehen was Playwright auf der Seite sieht.
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


async def debug_seite(page, url: str, name: str):
    """Speichert HTML + Screenshot für Diagnose"""
    print(f"\n  [DEBUG] Lade {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

    # 8 Sekunden warten für JS-Rendering
    print(f"  [DEBUG] Warte 8s auf JS-Rendering...")
    await page.wait_for_timeout(8_000)

    # HTML speichern
    html = await page.content()
    fname = f"debug_{name}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [DEBUG] HTML gespeichert → {fname} ({len(html)} Zeichen)")

    # Screenshot
    await page.screenshot(path=f"debug_{name}.png", full_page=False)
    print(f"  [DEBUG] Screenshot → debug_{name}.png")

    # Was ist auf der Seite?
    tabellen = await page.query_selector_all("table")
    print(f"  [DEBUG] Gefundene <table>-Elemente: {len(tabellen)}")

    rows = await page.query_selector_all("table tr")
    print(f"  [DEBUG] Gefundene <tr>-Elemente: {len(rows)}")

    # Alle relevanten Texte ausgeben
    body_text = await page.inner_text("body")
    zeilen = [z.strip() for z in body_text.split("\n") if z.strip()]
    print(f"  [DEBUG] Sichtbarer Text (erste 30 Zeilen):")
    for z in zeilen[:30]:
        print(f"         | {z}")

    return html, rows


async def scrape_mannschaft(page, team_name: str, mid: str, debug: bool = False) -> list[dict]:
    url = f"https://www.basketball-bund.net/mannschaft/{mid}/spielplan"
    spiele = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(6_000)  # JS braucht Zeit

        # Verschiedene Selektoren probieren
        selektoren = [
            "table tr",
            "[class*='spiel']",
            "[class*='game']",
            "[class*='match']",
            "[class*='result']",
            "tr[class*='row']",
        ]

        rows = []
        for sel in selektoren:
            rows = await page.query_selector_all(sel)
            if len(rows) > 2:
                print(f"  [INFO] Selektor '{sel}' → {len(rows)} Elemente")
                break

        if not rows:
            print(f"  ⚠  {team_name}: Keine Zeilen gefunden")
            return spiele

        for row in rows:
            try:
                zeile_text = (await row.inner_text() or "").strip()
                if not zeile_text:
                    continue

                # Datum in Zeile vorhanden?
                datum = parse_datum(zeile_text)
                if not datum:
                    continue

                # BSG beteiligt?
                if not any(s in zeile_text.lower() for s in BSG_NAMEN):
                    continue

                cells = await row.query_selector_all("td, span, div")
                texte = []
                for cell in cells:
                    t = (await cell.inner_text() or "").strip()
                    if t:
                        texte.append(t)

                zeit = parse_zeit(zeile_text)

                # Teamnamen aus Links
                links = await row.query_selector_all("a")
                namen = []
                for lnk in links:
                    n = (await lnk.inner_text() or "").strip()
                    if n and len(n) > 2 and not re.match(r"^\d", n):
                        namen.append(n)

                heim = namen[0] if len(namen) > 0 else ""
                gast = namen[1] if len(namen) > 1 else ""

                # Ergebnis
                ergebnis, heim_sieg = ergebnis_sieg(zeile_text)

                spiele.append({
                    "datum":    datum,
                    "zeit":     zeit,
                    "team":     team_name,
                    "heim":     heim or "BSG Usinger Land",
                    "gast":     gast or "",
                    "ergebnis": ergebnis,
                    "heimSieg": heim_sieg,
                })
            except Exception:
                continue

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


async def cookie_schliessen(page):
    try:
        await page.goto("https://www.basketball-bund.net", wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(3_000)
        for btn in await page.query_selector_all("button, a"):
            txt = (await btn.inner_text() or "").lower()
            if any(w in txt for w in ["akzeptiere", "zustimmen", "alle akzeptieren", "accept", "ok"]):
                await btn.click()
                await page.wait_for_timeout(1_000)
                print("  ✓  Cookie-Banner geschlossen")
                return True
        print("  ℹ  Kein Cookie-Banner gefunden")
    except Exception as e:
        print(f"  ⚠  Cookie-Banner: {e}")
    return False


async def main():
    print("BSG Usinger Land — Spielplan Scraper (Debug-Modus)")
    print("=" * 52)

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

        # Cookie-Banner schließen
        await cookie_schliessen(page)

        # Debug: Herren-Seite genau analysieren
        print("\n--- DEBUG: Herren-Seite analysieren ---")
        await debug_seite(page, "https://www.basketball-bund.net/mannschaft/314544/spielplan", "herren")

        # Alle Teams scrapen
        print("\n--- Alle Teams scrapen ---")
        for team_name, mid in TEAMS.items():
            if mid in bereits_gescrapt:
                print(f"  ↩  {team_name}: übersprungen")
                continue
            bereits_gescrapt.add(mid)
            spiele = await scrape_mannschaft(page, team_name, mid)
            alle_spiele.extend(spiele)

        await browser.close()

    # Sortieren und deduplizieren
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

    print("\n" + "=" * 52)
    print(f"✓  {len(eindeutig)} Spiele → spiele.json")


if __name__ == "__main__":
    asyncio.run(main())
