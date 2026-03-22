"""
BSG Usinger Land — DBB Spielplan Scraper
=========================================
Strategie: Das Widget erzeugt einen iFrame mit einer direkten URL
zu basketball-bund.net/rest/... die JSON zurückgibt.
Wir fangen diese Netzwerk-Requests mit Playwright ab.

Das Widget-Script lädt den iFrame von:
  https://www.basketball-bund.net/rest/widget/mannschaft/{id}/...

Wir starten das Widget auf einer lokalen HTML-Seite und
lauschen auf alle Netzwerk-Requests die JSON zurückgeben.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from playwright.async_api import async_playwright

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

BSG_NAMEN = ["bsg usinger", "usinger land"]

# Gesammelte API-Endpoints die das Widget aufruft
gefundene_endpoints = []


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
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def ergebnis_sieg(text: str):
    m = re.search(r"(\d+)\s*:\s*(\d+)", text or "")
    if not m:
        return None, None
    h, g = int(m.group(1)), int(m.group(2))
    return f"{h}:{g}", h > g


def parse_spiel_aus_json(data, team_name: str) -> list[dict]:
    """Versucht Spieldaten aus einer JSON-Antwort zu extrahieren."""
    spiele = []
    if not isinstance(data, (list, dict)):
        return spiele

    items = data if isinstance(data, list) else data.get("spiele", data.get("games", data.get("matches", [])))
    if not isinstance(items, list):
        return spiele

    for item in items:
        if not isinstance(item, dict):
            continue
        # Verschiedene mögliche Feldnamen
        heim = item.get("heimMannschaft", item.get("heim", item.get("home", item.get("homeTeam", ""))))
        gast = item.get("gastMannschaft", item.get("gast", item.get("away", item.get("awayTeam", ""))))
        datum_raw = item.get("datum", item.get("date", item.get("spielDatum", "")))
        zeit_raw  = item.get("zeit", item.get("time", item.get("spielZeit", "")))
        erg_raw   = item.get("ergebnis", item.get("result", item.get("score", "")))

        if isinstance(heim, dict):
            heim = heim.get("name", heim.get("kurzname", ""))
        if isinstance(gast, dict):
            gast = gast.get("name", gast.get("kurzname", ""))

        if not heim or not gast:
            continue
        if not (ist_bsg(heim) or ist_bsg(gast)):
            continue

        datum = parse_datum(str(datum_raw))
        if not datum:
            continue

        ergebnis, heim_sieg = ergebnis_sieg(str(erg_raw)) if erg_raw else (None, None)

        spiele.append({
            "datum":    datum,
            "zeit":     parse_zeit(str(zeit_raw)),
            "team":     team_name,
            "heim":     str(heim),
            "gast":     str(gast),
            "ergebnis": ergebnis,
            "heimSieg": heim_sieg,
        })
    return spiele


async def intercept_widget_api(mid: str, team_name: str) -> list[dict]:
    """
    Lädt das Widget in einer Mini-HTML-Seite und fängt alle
    Netzwerk-Requests ab die JSON zurückgeben.
    """
    spiele = []
    json_responses = []

    html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<div id="w_{mid}"></div>
<script src="//www.basketball-bund.net/rest/widget/widgetjs"></script>
<script>
window.addEventListener('load', function() {{
  setTimeout(function() {{
    if (typeof widget !== 'undefined') {{
      widget.mannschaftswidget('w_{mid}', {{
        iframeWidth: 800,
        iframeHeight: 600,
        mannschaftsId: '{mid}',
        showRefreshButton: false,
        titleColor: 'FFFFFF',
        titleBgColor: 'F4620A',
        tapColor: 'FFFFFF',
        tapBgColor: '1E1E1E'
      }});
    }}
  }}, 1000);
}});
</script>
</body>
</html>"""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="de-DE",
        )
        page = await context.new_page()

        # Alle Netzwerk-Requests abfangen
        async def on_response(response):
            url = response.url
            if "basketball-bund.net" in url and response.status == 200:
                ctype = response.headers.get("content-type", "")
                if "json" in ctype or "javascript" in ctype:
                    try:
                        text = await response.text()
                        if len(text) > 100 and ("{" in text or "[" in text):
                            gefundene_endpoints.append(url)
                            try:
                                data = json.loads(text)
                                json_responses.append((url, data))
                                print(f"     API: {url[:80]}")
                            except Exception:
                                pass
                    except Exception:
                        pass

        page.on("response", on_response)

        # Mini-HTML als Data-URL laden
        import base64
        encoded = base64.b64encode(html_content.encode()).decode()
        await page.goto(f"data:text/html;base64,{encoded}", wait_until="domcontentloaded")

        # Warten bis Widget-Requests abgeschlossen
        await page.wait_for_timeout(15_000)

        # iFrame-Inhalt lesen falls vorhanden
        frames = page.frames
        for frame in frames:
            if frame == page.main_frame:
                continue
            try:
                frame_url = frame.url
                print(f"     iFrame-URL: {frame_url[:80]}")
                rows = await frame.query_selector_all("table tr")
                for row in rows:
                    cells = await row.query_selector_all("td")
                    if len(cells) < 3:
                        continue
                    texte = [((await c.inner_text()) or "").strip() for c in cells]
                    zeile = " ".join(texte)
                    datum = parse_datum(zeile)
                    if not datum:
                        continue
                    if not any(s in zeile.lower() for s in BSG_NAMEN):
                        continue
                    links = await row.query_selector_all("a")
                    namen = [(await l.inner_text() or "").strip() for l in links]
                    namen = [n for n in namen if n and len(n) > 2]
                    heim = namen[0] if len(namen) > 0 else ""
                    gast = namen[1] if len(namen) > 1 else ""
                    ergebnis, heim_sieg = None, None
                    for t in reversed(texte):
                        e, s = ergebnis_sieg(t)
                        if e:
                            ergebnis, heim_sieg = e, s
                            break
                    spiele.append({
                        "datum": datum,
                        "zeit": parse_zeit(zeile),
                        "team": team_name,
                        "heim": heim or "BSG Usinger Land",
                        "gast": gast or "",
                        "ergebnis": ergebnis,
                        "heimSieg": heim_sieg,
                    })
            except Exception as e:
                print(f"     iFrame-Fehler: {e}")

        # JSON-Responses nach Spielen durchsuchen
        for url, data in json_responses:
            found = parse_spiel_aus_json(data, team_name)
            if found:
                spiele.extend(found)
                print(f"     {len(found)} Spiele aus API-Response")

        await browser.close()

    return spiele


async def main():
    print("BSG Usinger Land — Widget-API Scraper")
    print("=" * 44)

    alle_spiele = []
    bereits_gescrapt = set()

    for team_name, mid in TEAMS.items():
        if mid in bereits_gescrapt:
            print(f"  ↩  {team_name}: übersprungen")
            continue
        bereits_gescrapt.add(mid)

        print(f"\n  → {team_name} (ID: {mid})")
        spiele = await intercept_widget_api(mid, team_name)

        # Duplikate weg
        seen, result = set(), []
        for s in spiele:
            key = (s["datum"], s["heim"], s["gast"])
            if key not in seen:
                seen.add(key)
                result.append(s)

        print(f"  ✓  {team_name}: {len(result)} Spiele")
        alle_spiele.extend(result)

    # Alle gefundenen API-Endpoints ausgeben
    if gefundene_endpoints:
        print("\n  Gefundene API-Endpoints:")
        for ep in set(gefundene_endpoints):
            print(f"    {ep}")

    # Global deduplizieren + sortieren
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

    print("\n" + "=" * 44)
    print(f"✓  {len(eindeutig)} Spiele → spiele.json")


if __name__ == "__main__":
    asyncio.run(main())
