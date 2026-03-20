"""
BSG Usinger Land — DBB Spielplan Scraper
========================================
Liest Spielplan + Ergebnisse aller 13 Teams von basketball-bund.net
und speichert sie als spiele.json

Installation:
    pip install requests beautifulsoup4

Lokal testen:
    python scrape_dbb.py

Ausgabe:
    spiele.json  — wird von der Website eingelesen
"""

import json
import re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# ── Konfiguration ────────────────────────────────────────────
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

BASE_URL  = "https://www.basketball-bund.net/mannschaft/{id}/spielplan"
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; BSG-Scraper/1.0)"}
BSG_NAMES = ["bsg usinger", "usinger land"]   # Erkennungsstrings für BSG

# ── Hilfsfunktionen ──────────────────────────────────────────

def ist_bsg(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in BSG_NAMES)

def parse_datum(text: str) -> str | None:
    """Verschiedene DBB-Datumsformate → ISO 'YYYY-MM-DD'"""
    text = text.strip()
    formate = ["%d.%m.%Y", "%d.%m.%y", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"]
    for fmt in formate:
        try:
            return datetime.strptime(text[:len(fmt)], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Datum via Regex extrahieren
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if m:
        tag, monat, jahr = m.groups()
        if len(jahr) == 2:
            jahr = "20" + jahr
        return f"{int(jahr):04d}-{int(monat):02d}-{int(tag):02d}"
    return None

def parse_zeit(text: str) -> str:
    """Uhrzeit aus Text extrahieren → 'HH:MM'"""
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return ""

def parse_ergebnis(text: str) -> tuple[str | None, bool | None]:
    """
    Ergebnis-Text → (score_string, heim_gewonnen)
    Rückgabe: ('74:61', True) oder (None, None) wenn noch kein Ergebnis
    """
    text = text.strip()
    m = re.search(r"(\d+)\s*:\s*(\d+)", text)
    if not m:
        return None, None
    h, g = int(m.group(1)), int(m.group(2))
    return f"{h}:{g}", h > g

# ── Hauptfunktion ─────────────────────────────────────────────

def scrape_team(team_name: str, mannschafts_id: str) -> list[dict]:
    url = BASE_URL.format(id=mannschafts_id)
    spiele = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ⚠ Fehler bei {team_name} ({mannschafts_id}): {e}")
        return spiele

    soup = BeautifulSoup(resp.text, "html.parser")

    # DBB verwendet Tabellen mit class "spielplan" oder ähnlichem
    # Wir suchen alle Tabellenzeilen die Spieldaten enthalten
    tabellen = soup.find_all("table")

    for tabelle in tabellen:
        zeilen = tabelle.find_all("tr")
        for zeile in zeilen:
            zellen = zeile.find_all(["td", "th"])
            if len(zellen) < 3:
                continue

            texte = [z.get_text(strip=True) for z in zellen]
            voller_text = " ".join(texte)

            # Datum suchen
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

            # Heim / Gast aus Zeile extrahieren
            # Typisches Format: Datum | Heimteam | Gastteam | Ergebnis
            heim = gast = ergebnis_str = ""
            heim_sieg = None

            if len(texte) >= 4:
                # Spalte 1 oder 2 = Datum, dann Heim, Gast, Ergebnis
                for i, t in enumerate(texte):
                    if parse_datum(t):
                        try:
                            heim         = texte[i + 1].strip()
                            gast         = texte[i + 2].strip()
                            ergebnis_raw = texte[i + 3] if i + 3 < len(texte) else ""
                            ergebnis_str, heim_sieg = parse_ergebnis(ergebnis_raw)
                        except IndexError:
                            pass
                        break

            # Fallback: Links in Zeile für Teamnamen
            if not heim:
                links = zeile.find_all("a")
                if len(links) >= 2:
                    heim = links[0].get_text(strip=True)
                    gast = links[1].get_text(strip=True)

            if not heim or not gast:
                continue

            # BSG muss beteiligt sein
            if not (ist_bsg(heim) or ist_bsg(gast)):
                continue

            spiele.append({
                "datum":     datum,
                "zeit":      zeit,
                "team":      team_name,
                "heim":      heim,
                "gast":      gast,
                "ergebnis":  ergebnis_str,   # None = noch kein Ergebnis
                "heimSieg":  heim_sieg,       # None = noch kein Ergebnis
            })

    # Duplikate entfernen (gleiche Teams können in mehreren Ligen auftauchen)
    gesehen = set()
    eindeutig = []
    for s in spiele:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in gesehen:
            gesehen.add(key)
            eindeutig.append(s)

    print(f"  ✓ {team_name}: {len(eindeutig)} Spiele gefunden")
    return eindeutig


def main():
    print("BSG Usinger Land — Spielplan Scraper")
    print("=" * 40)

    alle_spiele = []
    bereits_gescrapt = set()  # verhindert doppeltes Scraping gleicher IDs

    for team_name, mannschafts_id in TEAMS.items():
        if mannschafts_id in bereits_gescrapt:
            print(f"  ↩ {team_name}: ID {mannschafts_id} bereits abgerufen, übersprungen")
            continue
        bereits_gescrapt.add(mannschafts_id)
        spiele = scrape_team(team_name, mannschafts_id)
        alle_spiele.extend(spiele)

    # Nach Datum sortieren
    alle_spiele.sort(key=lambda s: s["datum"])

    ausgabe = {
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
        "anzahl":       len(alle_spiele),
        "spiele":       alle_spiele,
    }

    with open("spiele.json", "w", encoding="utf-8") as f:
        json.dump(ausgabe, f, ensure_ascii=False, indent=2)

    print("=" * 40)
    print(f"✓ {len(alle_spiele)} Spiele gespeichert → spiele.json")
    print(f"  Aktualisiert: {ausgabe['aktualisiert']}")


if __name__ == "__main__":
    main()
