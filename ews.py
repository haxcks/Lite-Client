import requests
from bs4 import BeautifulSoup
import urllib3
import time
from datetime import datetime, timedelta
import re
import json
import os
import pyttsx3
import threading

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ----------------- CONFIG -----------------
URL = "https://earthquake.phivolcs.dost.gov.ph/"
CHECK_INTERVAL = 150  # seconds
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DISCORD_WEBHOOK_URL = "{webhook}"  # Replace with your webhook
DEBUG = True
TARGET_KEYWORDS = [] # targets specific keywords like 'Manila'
SEEN_FILE = "seen_quakes.json"  # file where seen quake IDs are stored
# ------------------------------------------

def load_seen_quakes():
    """Load seen quake IDs from a JSON file."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception as e:
            print(f"[!] Error reading {SEEN_FILE}: {e}")
    return set()

def save_seen_quakes(seen):
    """Save seen quake IDs to a JSON file."""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error saving {SEEN_FILE}: {e}")

def fetch_page(url):
    session = requests.Session()
    try:
        page = session.get(url, headers=HEADERS, timeout=15, verify=False)
        if page.status_code == 200:
            page.encoding = 'utf-8'
            return page.text
        if DEBUG:
            print(f"[!] HTTP {page.status_code} from {url}")
    except Exception as e:
        print(f"[!] Fetch error: {e}")
    return None

def parse_datetime_from_cell(cell_text):
    cell_text = cell_text.strip()
    cell_text = re.sub(r'\s+', ' ', cell_text)
    fmts = ["%d %B %Y - %I:%M %p", "%d %B %Y - %H:%M", "%d %B %Y"]
    for f in fmts:
        try:
            return datetime.strptime(cell_text, f)
        except Exception:
            pass
    m = re.search(r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})', cell_text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d %B %Y")
        except Exception:
            pass
    return None

def normalize_text(s):
    if not s:
        return ""
    try:
        if "Ã" in s or "Â" in s:
            s = s.encode('latin1').decode('utf-8')
    except Exception:
        pass
    return re.sub(r'\s+', ' ', s).strip()

def speak(text, speed=160):
    engine = pyttsx3.init()
    engine.setProperty('rate', speed)
    engine.say(text)
    engine.runAndWait()

def parse_earthquakes(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr")
    quakes = []
    now = datetime.now()
    cutoff = now - timedelta(days=8)

    for i, row in enumerate(rows):
        cols = [normalize_text(c.get_text(" ", strip=True)) for c in row.find_all("td")]
        if len(cols) < 6:
            continue
        date_cell, lat_cell, lon_cell, depth_cell, mag_cell, location_cell = cols[:6]
        quake_dt = parse_datetime_from_cell(date_cell)
        if quake_dt is None:
            if DEBUG:
                print(f"[DBG] Could not parse date for row {i}: '{date_cell}'")
            continue
        if quake_dt < cutoff:
            continue

        try:
            if "Ã" in location_cell or "Â" in location_cell:
                location_cell = location_cell.encode('latin1').decode('utf-8')
        except Exception:
            pass

        quake = {
            "datetime": quake_dt,
            "date": quake_dt.strftime("%d %B %Y"),
            "time": quake_dt.strftime("%I:%M %p"),
            "lat": lat_cell,
            "lon": lon_cell,
            "depth": depth_cell,
            "mag": mag_cell,
            "location": location_cell,
        }

        quake_id = f"{quake_dt.isoformat()}|{mag_cell}|{lat_cell}|{lon_cell}|{location_cell}"
        quakes.append((quake_id, quake))

    return quakes

def send_discord_alert(quake):
    if not DISCORD_WEBHOOK_URL:
        if DEBUG:
            print("[DBG] Discord webhook disabled.")
        return

    try:
        mag_val = float(quake.get("mag", 0))
        if mag_val >= 6.0:
            color = 0xFF0000
        elif mag_val >= 5.5:
            color = 0xFFA500
        elif mag_val >= 4.5:
            color = 0xFFFF00
        else:
            color = 0x00FF00

        embed = {
            "title": f"🌋 Earthquake: {quake.get('location','Unknown')}",
            "color": color,
            "fields": [
                {"name": "Date", "value": quake.get("date", "N/A"), "inline": True},
                {"name": "Time", "value": quake.get("time", "N/A"), "inline": True},
                {"name": "Latitude", "value": quake.get("lat", "N/A"), "inline": True},
                {"name": "Longitude", "value": quake.get("lon", "N/A"), "inline": True},
                {"name": "Depth", "value": f"{quake.get('depth','N/A')} km", "inline": True},
                {"name": "Magnitude", "value": quake.get("mag", "N/A"), "inline": True},
                {"name": "Location", "value": quake.get("location", "N/A"), "inline": False},
            ],
            "footer": {"text": "Source: PHIVOLCS | Auto-alert system by Haxs"},
        }

        payload = {"embeds": [embed]}
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print("[📨] Alert sent to Discord.")
    except Exception as e:
        print(f"[!] Discord send error: {e}")

def monitor_phivolcs():
    print("=== PHIVOLCS Monitor (7-day filter, JSON persistence) ===")
    seen_quakes = load_seen_quakes()
    print(f"[i] Loaded {len(seen_quakes)} previously seen events.")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] fetching page...")
        html = fetch_page(URL)
        if not html:
            print("[!] Failed to fetch page this cycle.")
            time.sleep(CHECK_INTERVAL)
            continue

        parsed = parse_earthquakes(html)
        parsed.reverse()
        if not parsed:
            print("[✓] No recent events found on the page.")
            time.sleep(CHECK_INTERVAL)
            continue

        new_alerts = []
        for quake_id, quake in parsed:
            if quake_id in seen_quakes:
                if DEBUG:
                    print(f"[DBG] Already seen: {quake['date']} {quake['time']} {quake['location']} mag={quake['mag']}")
                continue

            if TARGET_KEYWORDS:
                lowloc = quake['location'].lower()
                if not any(k in lowloc for k in TARGET_KEYWORDS):
                    if DEBUG:
                        print(f"[DBG] Skipping (keyword filter): {quake['location']}")
                    continue

            seen_quakes.add(quake_id)
            new_alerts.append(quake)

        if new_alerts:
            for q in new_alerts:
                send_discord_alert(q)
                print("-" * 60)
                print("[📨] Alert sent to Discord.")
                print(f"[⚠] Earthquake detected near {q['location']}")
                print(f"Date: {q['date']} - {q['time']} (Philippine Time)")
                print(f"Latitude: {q['lat']}ºN | Longitude: {q['lon']}ºE")
                print(f"Depth: {q['depth']} km | Magnitude: {q['mag']}")
                print("-" * 60)
                if float(q['mag']) >= 6.0 and float(q['depth']) < 70:
                    message = (
                        "EARLY WARNING SYSTEM HAS DETECTED A CATASTROPHIC EARTHQUAKE NEAR YOUR AREA! "
                        "TAKE COVER NOW!, TAKE COVER NOW!, "
                        "WAIT UNTIL THE DISASTER IS OVER!"
                        )
                    speak(message)
                elif float(q['mag']) <= 5.9 and float(q['mag']) >= 4.0 and float(q['depth']) < 70:
                    speak("Possible minor damage, caution still advised")

            save_seen_quakes(seen_quakes)
            print(f"[💾] Saved {len(seen_quakes)} total seen quakes.")
        else:
            print("[✓] No new alerts after filtering.")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor_phivolcs()
