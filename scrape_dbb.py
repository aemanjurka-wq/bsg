"""
BSG Usinger Land — Spielplan via iCalendar (.ics)
==================================================
basketball-bund.net bietet für jede Mannschaft einen iCal-Download.
Das ist die robusteste Methode: strukturierte Daten, kein JS nötig,
kein Scraping von HTML.

iCal-URL Schema:
  https://www.basketball-bund.net/mannschaft/{id}/ical

Installation:
  pip install requests icalendar

Lokal testen:
  python scrape_dbb.py
"""

import json
import re
import requests
from datetime import datetime, timezone, date
from icalendar import Calendar

# ── Konfiguration ────────────────────────────────────────────
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

# Mögliche iCal-URL-Varianten von basketball-bund.net
ICAL_URLS = [
    "https://www.basketball-bund.net/mannschaft/{id}/ical",
    "https://www.basketball-bund.net/rest/mannschaft/{id}/ical",
    "https://www.basketball-bund.net/mannschaft/{id}/spielplan/ical",
]

BSG_NAMEN = ["bsg usinger", "usinger land"]
HEADERS   = {
    "User-Agent": "Mozilla/5.0 (compatible; BSG-Kalender/1.0)",
    "Accept": "text/calendar, */*"
}


def ist_bsg(name: str) -> bool:
    if not name:
        return False
    n = name.lower().strip()
    return any(s in n for s in BSG_NAMEN)


def ergebnis_und_sieg(summary: str) -> tuple[str | None, bool | None]:
    """Ergebnis aus SUMMARY/DESCRIPTION extrahieren: '74:61' → ('74:61', True)"""
    m = re.search(r"(\d+)\s*:\s*(\d+)", summary or "")
    if not m:
        return None, None
    h, g = int(m.group(1)), int(m.group(2))
    return f"{h}:{g}", h > g


def hol_ical(mannschafts_id: str) -> bytes | None:
    """Versucht verschiedene iCal-URL-Varianten"""
    for url_tmpl in ICAL_URLS:
        url = url_tmpl.format(id=mannschafts_id)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200 and b"BEGIN:VCALENDAR" in r.content:
                return r.content
        except requests.RequestException:
            continue
    return None


def parse_ical(team_name: str, inhalt: bytes) -> list[dict]:
    spiele = []
    try:
        cal = Calendar.from_ical(inhalt)
    except Exception as e:
        print(f"  ⚠ iCal-Parse-Fehler: {e}")
        return spiele

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", ""))
        description = str(component.get("DESCRIPTION", ""))
        dtstart = component.get("DTSTART")

        if not dtstart:
            continue

        # Datum und Zeit extrahieren
        dt = dtstart.dt
        if isinstance(dt, datetime):
            datum = dt.strftime("%Y-%m-%d")
            zeit  = dt.strftime("%H:%M")
        elif isinstance(dt, date):
            datum = dt.strftime("%Y-%m-%d")
            zeit  = ""
        else:
            continue

        # Heim und Gast aus SUMMARY parsen
        # Typisches Format: "BSG Usinger Land vs. TSV Bad Homburg"
        # oder "Heim - Gast" oder "Heim : Gast"
        heim = gast = ""
        for sep in [" vs. ", " vs ", " - ", " : ", " – "]:
            if sep in summary:
                teile = summary.split(sep, 1)
                heim  = teile[0].strip()
                # Ergebnis aus dem Gast-Teil herausschneiden
                gast_roh = teile[1].strip()
                gast = re.sub(r"\d+\s*:\s*\d+", "", gast_roh).strip(" ()-")
                break

        if not heim:
            # Fallback: beide Teams aus Description
            m = re.search(r"Heim[:\s]+(.+?)\n.*Gast[:\s]+(.+)", description, re.I)
            if m:
                heim = m.group(1).strip()
                gast = m.group(2).strip()

        # BSG muss beteiligt sein
        if not (ist_bsg(heim) or ist_bsg(gast) or ist_bsg(summary)):
            # Viele iCal-Events sind Liga-weit — nur BSG-Spiele behalten
            if "usinger" not in summary.lower() and "bsg" not in summary.lower():
                continue

        # Ergebnis suchen (in SUMMARY oder DESCRIPTION)
        ergebnis_str, heim_sieg = ergebnis_und_sieg(summary)
        if not ergebnis_str:
            ergebnis_str, heim_sieg = ergebnis_und_sieg(description)

        spiele.append({
            "datum":    datum,
            "zeit":     zeit,
            "team":     team_name,
            "heim":     heim or summary,
            "gast":     gast,
            "ergebnis": ergebnis_str,
            "heimSieg": heim_sieg,
        })

    return spiele


def main():
    print("BSG Usinger Land — iCal Spielplan Scraper")
    print("=" * 44)

    alle_spiele      = []
    bereits_gescrapt = set()

    for team_name, mid in TEAMS.items():
        if mid in bereits_gescrapt:
            print(f"  ↩ {team_name}: ID {mid} bereits abgerufen")
            continue
        bereits_gescrapt.add(mid)

        print(f"  → {team_name} (ID: {mid})")
        inhalt = hol_ical(mid)

        if not inhalt:
            print(f"  ⚠ Kein iCal für {team_name} — versuche HTML-Fallback")
            # HTML-Fallback: einfacher requests-Abruf der Spielplan-Seite
            try:
                url = f"https://www.basketball-bund.net/mannschaft/{mid}/spielplan"
                r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
                if r.status_code == 200:
                    # Ergebnisse per Regex direkt aus HTML fischen
                    matches = re.findall(
                        r'(\d{2}\.\d{2}\.\d{4})[^<]*?(\d{1,2}:\d{2})[^<]*?'
                        r'([A-Za-zÄÖÜäöüß\s\.\-]+?)\s*(?:vs\.?|-|:)\s*'
                        r'([A-Za-zÄÖÜäöüß\s\.\-]+?)(?:\s+(\d+:\d+))?',
                        r.text
                    )
                    for m in matches:
                        datum_raw, zeit_raw, heim_raw, gast_raw, erg_raw = m
                        if not (ist_bsg(heim_raw) or ist_bsg(gast_raw)):
                            continue
                        d = datetime.strptime(datum_raw, "%d.%m.%Y")
                        ergebnis_str, heim_sieg = ergebnis_und_sieg(erg_raw) if erg_raw else (None, None)
                        alle_spiele.append({
                            "datum":    d.strftime("%Y-%m-%d"),
                            "zeit":     zeit_raw,
                            "team":     team_name,
                            "heim":     heim_raw.strip(),
                            "gast":     gast_raw.strip(),
                            "ergebnis": ergebnis_str,
                            "heimSieg": heim_sieg,
                        })
                    print(f"     HTML-Fallback: gefunden")
            except Exception as e:
                print(f"  ✗ Auch HTML-Fallback fehlgeschlagen: {e}")
            continue

        spiele = parse_ical(team_name, inhalt)
        alle_spiele.extend(spiele)
        print(f"     {len(spiele)} Spiele aus iCal")

    # Duplikate entfernen und sortieren
    gesehen, eindeutig = set(), []
    for s in alle_spiele:
        key = (s["datum"], s.get("heim", ""), s.get("gast", ""))
        if key not in gesehen:
            gesehen.add(key)
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
    print(f"✓ {len(eindeutig)} Spiele → spiele.json")


if __name__ == "__main__":
    main()
