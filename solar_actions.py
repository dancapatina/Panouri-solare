# ============================================================
# FUSIONSOLAR — GitHub Actions version (optimizat)
# Istoric: extragere LUNARĂ (1 cerere/lună, rapid)
# Luna curentă: extragere zilnică
# Total timp estimat: 3-5 minute
# ============================================================

import json, os, time, zoneinfo
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

# ============================================================
# CONFIGURARE
# ============================================================
USER       = os.environ.get("FUSIONSOLAR_USER", "dan.dan")
PASS       = os.environ.get("FUSIONSOLAR_PASS", "")
STATION_DN = "NE=145965438"
DATA_START = "2023-06-23"
TZ_STR     = "Europe/Bucharest"
TZ_OFF     = 2.0
LOGIN_URL  = "https://eu5.fusionsolar.huawei.com"
BASE_URL   = "https://uni004eu5.fusionsolar.huawei.com"
JSON_OUT   = "fusionsolar_data.json"
LOGIN_TIMEOUT = 40   # secunde max pentru login

TZ = zoneinfo.ZoneInfo(TZ_STR)

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(msg):
    print(f"[{ts()}] {msg}", flush=True)

def date_to_ms(d: date) -> int:
    dt = datetime(d.year, d.month, d.day, tzinfo=TZ)
    return int(dt.timestamp() * 1000)

def date_to_str(d: date) -> str:
    return f"{d.strftime('%Y-%m-%d')} 00:00:00"

# ============================================================
# SELENIUM LOGIN
# ============================================================
def creeaza_driver():
    opts = ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(30)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def login_selenium(driver) -> bool:
    log(f"Deschid {LOGIN_URL} ...")
    try:
        driver.get(LOGIN_URL)
    except Exception as e:
        log(f"Eroare la încărcare pagină: {e}")
        return False

    # Aștept max LOGIN_TIMEOUT secunde pentru inputuri
    log(f"Aștept câmpuri login (max {LOGIN_TIMEOUT}s)...")
    try:
        WebDriverWait(driver, LOGIN_TIMEOUT).until(
            lambda d: len([e for e in d.find_elements(By.TAG_NAME, "input") if e.is_displayed()]) >= 2
        )
    except TimeoutException:
        log(f"❌ Timeout: câmpurile de login nu au apărut în {LOGIN_TIMEOUT}s!")
        log(f"URL curent: {driver.current_url}")
        log(f"Titlu pagină: {driver.title}")
        return False

    vizibile = [e for e in driver.find_elements(By.TAG_NAME, "input") if e.is_displayed()]
    log(f"Inputuri găsite: {len(vizibile)}")

    try:
        vizibile[0].clear()
        vizibile[0].send_keys(USER)
        time.sleep(0.3)
        vizibile[1].clear()
        vizibile[1].send_keys(PASS)
        time.sleep(0.3)
    except Exception as e:
        log(f"Eroare completare câmpuri: {e}")
        return False

    # Buton submit
    btn = None
    for by, sel in [
        (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'log')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, ".login-btn"),
        (By.CSS_SELECTOR, "button.btn-primary"),
        (By.XPATH, "//button[@type='submit']"),
    ]:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                btn = el
                break
        except NoSuchElementException:
            pass

    if btn:
        btn.click()
        log("Buton login apăsat.")
    else:
        vizibile[1].send_keys("\n")
        log("Enter trimis.")

    # Aștept redirect după login (max 30s)
    log("Aștept redirect după login...")
    try:
        WebDriverWait(driver, 30).until(
            lambda d: "login" not in d.current_url.lower() or "overview" in d.current_url.lower()
        )
    except TimeoutException:
        # Verificăm totuși URL-ul
        pass

    url = driver.current_url
    log(f"URL după login: {url}")

    if "login" in url.lower() and "overview" not in url.lower():
        log("❌ Login eșuat — tot pe pagina de login.")
        return False

    log("✅ Login reușit!")
    return True


def extrage_session(driver) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": BASE_URL,
        "Accept": "application/json, text/plain, */*",
    })
    for url in [LOGIN_URL, BASE_URL]:
        try:
            driver.get(url)
            time.sleep(2)
            for c in driver.get_cookies():
                sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        except Exception as e:
            log(f"  Eroare cookies {url}: {e}")
    log(f"Cookies extrase: {len(sess.cookies)}")
    return sess

# ============================================================
# API — ZILNIC (timeDim=2)
# ============================================================
def api_zi(sess, d: date) -> dict | None:
    dn = STATION_DN.replace("=", "%3D")
    tz = TZ_STR.replace("/", "%2F")
    ds = date_to_str(d).replace(" ", "%20").replace(":", "%3A")
    url = (
        f"{BASE_URL}/rest/pvms/web/station/v3/overview/energy-balance"
        f"?stationDn={dn}&timeDim=2&timeZone={TZ_OFF}"
        f"&timeZoneStr={tz}&queryTime={date_to_ms(d)}&dateStr={ds}&_={int(time.time()*1000)}"
    )
    try:
        r = sess.get(url, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log(f"  Eroare API zi {d}: {e}")
        return None

# ============================================================
# API — LUNAR (timeDim=3) — mult mai rapid pentru istoric
# ============================================================
def api_luna(sess, an: int, luna: int) -> dict | None:
    d = date(an, luna, 1)
    dn = STATION_DN.replace("=", "%3D")
    tz = TZ_STR.replace("/", "%2F")
    ds = date_to_str(d).replace(" ", "%20").replace(":", "%3A")
    url = (
        f"{BASE_URL}/rest/pvms/web/station/v3/overview/energy-balance"
        f"?stationDn={dn}&timeDim=3&timeZone={TZ_OFF}"
        f"&timeZoneStr={tz}&queryTime={date_to_ms(d)}&dateStr={ds}&_={int(time.time()*1000)}"
    )
    try:
        r = sess.get(url, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log(f"  Eroare API lună {an}-{luna:02d}: {e}")
        return None

# ============================================================
# PARSE
# ============================================================
def parse_vals(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None

    def gv(*keys):
        for k in keys:
            v = payload.get(k)
            if v is not None and v != "--" and v != "":
                try:
                    f = float(v)
                    if f >= 0:
                        return f
                except (ValueError, TypeError):
                    pass
        return None

    pv     = gv("totalProductPower", "productPower", "pvPower", "generationPower")
    livrat = gv("totalOnGridPower",  "onGridPower",  "feedInPower", "exportPower")
    din_r  = gv("totalBuyPower",     "buyPower",     "purchasePower", "fromGridPower")
    consum = gv("totalUsePower",     "consumePower", "loadPower", "totalLoadPower")

    if consum is None and pv is not None and livrat is not None and din_r is not None:
        consum = max(0.0, pv - livrat) + din_r

    if (pv or 0) == 0 and (livrat or 0) == 0:
        return None

    return {
        "pv_kwh":        round(pv     or 0.0, 2),
        "livrat_kwh":    round(livrat or 0.0, 2),
        "din_retea_kwh": round(din_r  or 0.0, 2),
        "consum_kwh":    round(consum or 0.0, 2),
    }


def extrage_payload(raw: dict) -> dict | None:
    if raw is None:
        return None
    payload = raw.get("data", raw)
    # Lunar returnează listă de zile — luăm totalul (ultima intrare sau sum)
    if isinstance(payload, list):
        totale = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            for k, v in item.items():
                if isinstance(v, (int, float)) and v >= 0:
                    totale[k] = totale.get(k, 0) + v
        return parse_vals(totale) if totale else None
    return parse_vals(payload)

# ============================================================
# API LIVE (putere instantanee)
# ============================================================
def api_live(sess) -> dict:
    url = (
        f"{BASE_URL}/rest/pvms/web/station/v3/overview/station-real-kpi"
        f"?stationDn={STATION_DN.replace('=','%3D')}&_={int(time.time()*1000)}"
    )
    try:
        r = sess.get(url, timeout=15)
        if r.status_code == 200:
            p = r.json().get("data", {})
            if isinstance(p, dict):
                def gv(*keys):
                    for k in keys:
                        v = p.get(k)
                        if v is not None:
                            try: return round(float(v), 2)
                            except: pass
                    return None
                return {
                    "pv_kw":           gv("productPower","pvPower","realTimePower"),
                    "livrat_retea_kw": gv("onGridPower","feedInPower"),
                    "din_retea_kw":    gv("buyPower","purchasePower"),
                }
    except Exception as e:
        log(f"Eroare live: {e}")
    return {}

# ============================================================
# DATE EXISTENTE
# ============================================================
def incarca_json(path: str) -> dict:
    zile = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            for zi_str, v in d.get("zile", {}).items():
                pv     = v.get("pv_kwh_sum", v.get("pv_kwh", 0))
                livrat = v.get("livrat_kwh_sum", v.get("livrat_kwh", 0))
                din_r  = v.get("din_retea_kwh_sum", v.get("din_retea_kwh", 0))
                consum = v.get("consum_kwh_sum", v.get("consum_kwh", 0))
                din_pv = round(max(0.0, pv - livrat), 2)
                zile[zi_str] = {
                    "pv_kwh": pv, "livrat_kwh": livrat,
                    "din_pv_kwh": din_pv, "din_retea_kwh": din_r,
                    "consum_kwh": consum,
                }
            log(f"Date existente: {len(zile)} zile.")
        except Exception as e:
            log(f"Eroare citire JSON: {e}")
    return zile

# ============================================================
# GENERARE JSON FINAL
# ============================================================
def stats_grup(vals: list) -> dict:
    if not vals:
        return {"sum": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "sum": round(sum(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "avg": round(sum(vals) / len(vals), 2),
    }

METRICI = ["pv", "livrat", "din_pv", "din_retea", "consum"]
KEYS    = ["pv_kwh", "livrat_kwh", "din_pv_kwh", "din_retea_kwh", "consum_kwh"]

def genereaza_json(zile: dict, live: dict) -> dict:
    zile_ok = {k: v for k, v in zile.items() if v["livrat_kwh"] > 0}

    zile_out = {}
    for zi_str, v in sorted(zile_ok.items()):
        zile_out[zi_str] = {}
        for m, k in zip(METRICI, KEYS):
            val = v[k]
            zile_out[zi_str].update({
                f"{m}_kwh_sum": val, f"{m}_kwh_min": val,
                f"{m}_kwh_max": val, f"{m}_kwh_avg": val,
            })

    luni_g: dict[str, list] = {}
    for zi_str, v in zile_ok.items():
        luni_g.setdefault(zi_str[:7], []).append(v)

    luni_out = {}
    for luna, lst in sorted(luni_g.items()):
        luni_out[luna] = {}
        for m, k in zip(METRICI, KEYS):
            sg = stats_grup([x[k] for x in lst])
            luni_out[luna].update({
                f"{m}_kwh_sum": sg["sum"], f"{m}_kwh_min": sg["min"],
                f"{m}_kwh_max": sg["max"], f"{m}_kwh_avg": sg["avg"],
            })

    ani_g: dict[str, list] = {}
    for zi_str, v in zile_ok.items():
        ani_g.setdefault(zi_str[:4], []).append(v)

    ani_out = {}
    for an, lst in sorted(ani_g.items()):
        ani_out[an] = {}
        for m, k in zip(METRICI, KEYS):
            sg = stats_grup([x[k] for x in lst])
            ani_out[an].update({
                f"{m}_kwh_sum": sg["sum"], f"{m}_kwh_min": sg["min"],
                f"{m}_kwh_max": sg["max"], f"{m}_kwh_avg": sg["avg"],
            })

    return {
        "actualizat": datetime.now(zoneinfo.ZoneInfo(TZ_STR)).strftime("%Y-%m-%d %H:%M:%S"),
        "live": live,
        "zile": zile_out,
        "luni": luni_out,
        "ani":  ani_out,
    }

# ============================================================
# MAIN
# ============================================================
def main():
    log("=" * 50)
    log("FUSIONSOLAR — GitHub Actions (versiune optimizată)")
    log(f"Pornit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 50)

    if not PASS:
        log("❌ FUSIONSOLAR_PASS lipsește din Secrets!")
        return

    driver = creeaza_driver()
    try:
        if not login_selenium(driver):
            log("❌ Login eșuat! Opresc.")
            return
        sess = extrage_session(driver)
    finally:
        driver.quit()
        log("Browser închis.")

    # Date existente
    zile = incarca_json(JSON_OUT)

    azi       = date.today()
    luna_curenta = azi.strftime("%Y-%m")
    data_start   = date.fromisoformat(DATA_START)

    # ── EXTRAGERE LUNARĂ pentru lunile complete istorice ──────────────
    # O lună completă = luna_str < luna_curentă și nu există nicio zi din ea
    luni_existente = set(zi[:7] for zi in zile)
    luna_it = date(data_start.year, data_start.month, 1)

    luni_de_extras = []
    while luna_it.strftime("%Y-%m") < luna_curenta:
        luna_str = luna_it.strftime("%Y-%m")
        if luna_str not in luni_existente:
            luni_de_extras.append(luna_it)
        # Dacă există parțial (câteva zile), lasă-o — o completăm zi cu zi mai jos
        luna_urm = date(luna_it.year + (luna_it.month // 12), (luna_it.month % 12) + 1, 1)
        luna_it = luna_urm

    if luni_de_extras:
        log(f"Extrag {len(luni_de_extras)} luni istorice (timeDim=3)...")
        for l in luni_de_extras:
            raw = api_luna(sess, l.year, l.month)
            vals = extrage_payload(raw)
            if vals:
                # Salvăm ca o singură zi reprezentativă (prima din lună)
                # cu datele totale ale lunii — pentru tab Luni/Ani
                # Ulterior completăm cu date zilnice dacă e nevoie
                zi_repr = str(l)
                din_pv = round(max(0.0, vals["pv_kwh"] - vals["livrat_kwh"]), 2)
                zile[zi_repr] = {
                    "pv_kwh": vals["pv_kwh"], "livrat_kwh": vals["livrat_kwh"],
                    "din_pv_kwh": din_pv, "din_retea_kwh": vals["din_retea_kwh"],
                    "consum_kwh": vals["consum_kwh"],
                    "_lunar": True,  # marcaj că e total lunar, nu zilnic
                }
                log(f"  {l.strftime('%Y-%m')}: PV={vals['pv_kwh']} kWh")
            time.sleep(0.3)
        log(f"✅ Extrase {len(luni_de_extras)} luni.")
    else:
        log("Toate lunile istorice sunt deja prezente.")

    # ── EXTRAGERE ZILNICĂ pentru luna curentă ─────────────────────────
    # Prima zi din luna curentă
    prima_zi_luna = date(azi.year, azi.month, 1)
    zile_existente_luna = {z for z in zile if z.startswith(luna_curenta) and not zile[z].get("_lunar")}

    d = prima_zi_luna
    log(f"Extrag zilnic luna curentă ({luna_curenta})...")
    while d <= azi:
        zi_str = str(d)
        if zi_str not in zile_existente_luna or d == azi:
            raw  = api_zi(sess, d)
            vals = extrage_payload(raw)
            if vals:
                din_pv = round(max(0.0, vals["pv_kwh"] - vals["livrat_kwh"]), 2)
                zile[zi_str] = {
                    "pv_kwh": vals["pv_kwh"], "livrat_kwh": vals["livrat_kwh"],
                    "din_pv_kwh": din_pv, "din_retea_kwh": vals["din_retea_kwh"],
                    "consum_kwh": vals["consum_kwh"],
                }
                log(f"  {zi_str}: PV={vals['pv_kwh']} kWh")
        time.sleep(0.2)
        d += timedelta(days=1)

    # Live
    live = api_live(sess)
    if str(azi) in zile and not zile[str(azi)].get("_lunar"):
        v = zile[str(azi)]
        live.update({
            "azi_pv_kwh":        v["pv_kwh"],
            "azi_livrat_kwh":    v["livrat_kwh"],
            "azi_din_pv_kwh":    v["din_pv_kwh"],
            "azi_din_retea_kwh": v["din_retea_kwh"],
            "azi_consum_kwh":    v["consum_kwh"],
        })
        log(f"Live azi: PV={v['pv_kwh']} kWh | Livrat={v['livrat_kwh']} kWh")

    # Salvare
    data_out = genereaza_json(zile, live)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(data_out, f, indent=2, ensure_ascii=False)

    log(f"✅ JSON salvat: {len(data_out['zile'])} zile | {len(data_out['luni'])} luni")
    log(f"Terminat: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
