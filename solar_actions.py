# ============================================================
# FUSIONSOLAR — GitHub Actions version
# Rulează în cloud, fără PC, la fiecare 10 minute
# Credențiale din GitHub Secrets: FUSIONSOLAR_USER / FUSIONSOLAR_PASS
# ============================================================

import json, os, time, zoneinfo
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# ============================================================
# CONFIGURARE — credențiale din GitHub Secrets
# ============================================================
USER         = os.environ.get("FUSIONSOLAR_USER", "dan.dan")
PASS         = os.environ.get("FUSIONSOLAR_PASS", "")
STATION_DN   = "NE=145965438"
DATA_START   = "2023-06-23"
TZ_STR       = "Europe/Bucharest"
TZ_OFF       = 2.0
LOGIN_URL    = "https://eu5.fusionsolar.huawei.com"
BASE_URL     = "https://uni004eu5.fusionsolar.huawei.com"
JSON_OUT     = "fusionsolar_data.json"

TZ = zoneinfo.ZoneInfo(TZ_STR)

# ============================================================
# UTILITARE
# ============================================================
def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(msg):
    print(f"[{ts()}] {msg}", flush=True)

def date_to_ms(d: date) -> int:
    dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=TZ)
    return int(dt.timestamp() * 1000)

def date_to_str(d: date) -> str:
    return f"{d.strftime('%Y-%m-%d')} 00:00:00"

# ============================================================
# LOGIN SELENIUM (Chrome headless)
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
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def login_selenium(driver) -> bool:
    log(f"Login la {LOGIN_URL} ...")
    driver.get(LOGIN_URL)
    time.sleep(6)

    toate = driver.find_elements(By.TAG_NAME, "input")
    vizibile = [el for el in toate if el.is_displayed()]
    log(f"Inputuri vizibile: {len(vizibile)}")

    if len(vizibile) < 2:
        log("❌ Prea puține câmpuri!")
        return False

    vizibile[0].click(); time.sleep(0.3)
    vizibile[0].clear()
    vizibile[0].send_keys(USER)
    time.sleep(0.4)
    vizibile[1].click(); time.sleep(0.3)
    vizibile[1].clear()
    vizibile[1].send_keys(PASS)
    time.sleep(0.4)

    btn = None
    for by, sel in [
        (By.XPATH, "//button[contains(text(),'Log')]"),
        (By.XPATH, "//button[contains(text(),'Sign')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, ".login-btn"),
        (By.CSS_SELECTOR, "button.btn-primary"),
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
    else:
        vizibile[1].send_keys("\n")

    log("Aștept dashboard...")
    time.sleep(12)

    url = driver.current_url
    log(f"URL curent: {url}")
    if "login" in url.lower() and "overview" not in url.lower():
        log("❌ Login eșuat!")
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
        "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    })
    for domain_url in [LOGIN_URL, BASE_URL]:
        try:
            driver.get(domain_url)
            time.sleep(2)
            for c in driver.get_cookies():
                sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        except Exception as e:
            log(f"Eroare cookies {domain_url}: {e}")
    log(f"Cookies extrase: {len(sess.cookies)}")
    return sess

# ============================================================
# API
# ============================================================
def api_zi(sess: requests.Session, d: date) -> dict | None:
    dn_enc = STATION_DN.replace("=", "%3D")
    tz_enc = TZ_STR.replace("/", "%2F")
    ts_ms  = date_to_ms(d)
    ds_enc = date_to_str(d).replace(" ", "%20").replace(":", "%3A")
    cache  = int(time.time() * 1000)
    url = (
        f"{BASE_URL}/rest/pvms/web/station/v3/overview/energy-balance"
        f"?stationDn={dn_enc}&timeDim=2&timeZone={TZ_OFF}"
        f"&timeZoneStr={tz_enc}&queryTime={ts_ms}&dateStr={ds_enc}&_={cache}"
    )
    try:
        r = sess.get(url, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        log(f"  Eroare API {d}: {e}")
        return None


def parse_zi(raw: dict) -> dict | None:
    if raw is None:
        return None
    payload = raw.get("data", raw)
    if not isinstance(payload, dict):
        if isinstance(payload, list) and len(payload) > 0:
            payload = payload[0]
        else:
            return None

    def get_val(*keys):
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

    pv     = get_val("totalProductPower", "productPower", "pvPower", "generationPower")
    livrat = get_val("totalOnGridPower",  "onGridPower",  "feedInPower", "exportPower")
    din_r  = get_val("totalBuyPower",     "buyPower",     "purchasePower", "fromGridPower")
    consum = get_val("totalUsePower",     "consumePower", "loadPower", "totalLoadPower")

    if consum is None and pv is not None and livrat is not None and din_r is not None:
        consum = max(0.0, pv - livrat) + din_r

    if (pv or 0) == 0 and (livrat or 0) == 0:
        return None

    return {
        "pv_kwh":        pv     or 0.0,
        "livrat_kwh":    livrat or 0.0,
        "din_retea_kwh": din_r  or 0.0,
        "consum_kwh":    consum or 0.0,
    }


def api_live(sess: requests.Session) -> dict:
    url = (
        f"{BASE_URL}/rest/pvms/web/station/v3/overview/station-real-kpi"
        f"?stationDn={STATION_DN.replace('=','%3D')}&_={int(time.time()*1000)}"
    )
    try:
        r = sess.get(url, timeout=15)
        if r.status_code == 200:
            payload = r.json().get("data", {})
            if isinstance(payload, dict):
                def gv(*keys):
                    for k in keys:
                        v = payload.get(k)
                        if v is not None:
                            try: return round(float(v), 2)
                            except: pass
                    return None
                return {
                    "pv_kw":             gv("productPower","pvPower","realTimePower"),
                    "livrat_retea_kw":   gv("onGridPower","feedInPower","gridPower"),
                    "din_retea_kw":      gv("buyPower","purchasePower","fromGridPower"),
                    "randament_azi_kwh": gv("dailyEnergy","todayEnergy","dayPower","dailyYield"),
                }
    except Exception as e:
        log(f"Eroare live: {e}")
    return {}

# ============================================================
# MANAGER DATE
# ============================================================
def stats_grup(valori: list) -> dict:
    if not valori:
        return {"sum": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "sum": round(sum(valori), 2),
        "min": round(min(valori), 2),
        "max": round(max(valori), 2),
        "avg": round(sum(valori) / len(valori), 2),
    }


def incarca_json(path: str) -> dict:
    """Încarcă JSON existent sau returnează structură goală."""
    zile = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            for zi_str, v in d.get("zile", {}).items():
                # Suportă ambele formate (vechi și nou)
                if "pv_kwh_sum" in v:
                    pv     = v["pv_kwh_sum"]
                    livrat = v["livrat_kwh_sum"]
                    din_r  = v.get("din_retea_kwh_sum", 0)
                    consum = v.get("consum_kwh_sum", 0)
                else:
                    pv     = v.get("pv_kwh", 0)
                    livrat = v.get("livrat_kwh", 0)
                    din_r  = v.get("din_retea_kwh", 0)
                    consum = v.get("consum_kwh", 0)
                din_pv = round(max(0.0, pv - livrat), 2)
                zile[zi_str] = {
                    "pv_kwh": pv, "livrat_kwh": livrat,
                    "din_pv_kwh": din_pv, "din_retea_kwh": din_r,
                    "consum_kwh": consum,
                }
            log(f"Încărcat {len(zile)} zile din JSON existent.")
        except Exception as e:
            log(f"Eroare încărcare JSON: {e}")
    return zile


def genereaza_json(zile: dict, live: dict) -> dict:
    METRICI = ["pv", "livrat", "din_pv", "din_retea", "consum"]
    KEYS    = ["pv_kwh", "livrat_kwh", "din_pv_kwh", "din_retea_kwh", "consum_kwh"]

    # Zile
    zile_out = {}
    for zi_str, v in sorted(zile.items()):
        if v["livrat_kwh"] <= 0:
            continue
        zile_out[zi_str] = {}
        for m, k in zip(METRICI, KEYS):
            val = v[k]
            zile_out[zi_str].update({
                f"{m}_kwh_sum": val,
                f"{m}_kwh_min": val,
                f"{m}_kwh_max": val,
                f"{m}_kwh_avg": val,
            })

    # Luni
    luni_grup: dict[str, list] = {}
    for zi_str, v in zile.items():
        if v["livrat_kwh"] <= 0:
            continue
        luni_grup.setdefault(zi_str[:7], []).append(v)

    luni_out = {}
    for luna, lst in sorted(luni_grup.items()):
        luni_out[luna] = {}
        for m, k in zip(METRICI, KEYS):
            sg = stats_grup([x[k] for x in lst])
            luni_out[luna].update({
                f"{m}_kwh_sum": sg["sum"],
                f"{m}_kwh_min": sg["min"],
                f"{m}_kwh_max": sg["max"],
                f"{m}_kwh_avg": sg["avg"],
            })

    # Ani
    ani_grup: dict[str, list] = {}
    for zi_str, v in zile.items():
        if v["livrat_kwh"] <= 0:
            continue
        ani_grup.setdefault(zi_str[:4], []).append(v)

    ani_out = {}
    for an, lst in sorted(ani_grup.items()):
        ani_out[an] = {}
        for m, k in zip(METRICI, KEYS):
            sg = stats_grup([x[k] for x in lst])
            ani_out[an].update({
                f"{m}_kwh_sum": sg["sum"],
                f"{m}_kwh_min": sg["min"],
                f"{m}_kwh_max": sg["max"],
                f"{m}_kwh_avg": sg["avg"],
            })

    return {
        "actualizat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "live":  live,
        "zile":  zile_out,
        "luni":  luni_out,
        "ani":   ani_out,
    }

# ============================================================
# MAIN
# ============================================================
def main():
    log("=" * 50)
    log("FUSIONSOLAR — GitHub Actions update")
    log(f"Pornit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 50)

    if not PASS:
        log("❌ FUSIONSOLAR_PASS lipsește din Secrets!")
        return

    # Login
    driver = creeaza_driver()
    try:
        if not login_selenium(driver):
            log("❌ Login eșuat!")
            return
        sess = extrage_session(driver)
    finally:
        driver.quit()
        log("Browser închis.")

    # Încarcă date existente
    zile = incarca_json(JSON_OUT)

    # Determină zilele lipsă
    data_start = date.fromisoformat(DATA_START)
    ieri = date.today() - timedelta(days=1)
    ultima = date.fromisoformat(sorted(zile.keys())[-1]) if zile else None

    if ultima and ultima >= ieri:
        log("Date istorice complete.")
    else:
        start = (ultima + timedelta(days=1)) if ultima else data_start
        log(f"Extrag din {start} până la {ieri}...")
        d = start
        extrase = 0
        while d <= ieri:
            raw = api_zi(sess, d)
            vals = parse_zi(raw)
            if vals:
                pv     = round(vals["pv_kwh"], 2)
                livrat = round(vals["livrat_kwh"], 2)
                din_r  = round(vals["din_retea_kwh"], 2)
                consum = round(vals["consum_kwh"], 2)
                din_pv = round(max(0.0, pv - livrat), 2)
                zile[str(d)] = {
                    "pv_kwh": pv, "livrat_kwh": livrat,
                    "din_pv_kwh": din_pv, "din_retea_kwh": din_r,
                    "consum_kwh": consum,
                }
                extrase += 1
            time.sleep(0.5)
            d += timedelta(days=1)
        log(f"Extrase {extrase} zile.")

    # Ziua curentă
    azi = date.today()
    raw_azi = api_zi(sess, azi)
    vals_azi = parse_zi(raw_azi)
    live = api_live(sess)

    if vals_azi:
        pv     = round(vals_azi["pv_kwh"], 2)
        livrat = round(vals_azi["livrat_kwh"], 2)
        din_r  = round(vals_azi["din_retea_kwh"], 2)
        consum = round(vals_azi["consum_kwh"], 2)
        din_pv = round(max(0.0, pv - livrat), 2)
        zile[str(azi)] = {
            "pv_kwh": pv, "livrat_kwh": livrat,
            "din_pv_kwh": din_pv, "din_retea_kwh": din_r,
            "consum_kwh": consum,
        }
        live.update({
            "azi_pv_kwh":        pv,
            "azi_livrat_kwh":    livrat,
            "azi_din_pv_kwh":    din_pv,
            "azi_din_retea_kwh": din_r,
            "azi_consum_kwh":    consum,
        })
        log(f"Azi: PV={pv} kWh | Livrat={livrat} kWh | Consum={consum} kWh")

    # Salvează JSON
    data_out = genereaza_json(zile, live)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(data_out, f, indent=2, ensure_ascii=False)
    log(f"✅ JSON salvat: {JSON_OUT} ({len(data_out['zile'])} zile)")


if __name__ == "__main__":
    main()
