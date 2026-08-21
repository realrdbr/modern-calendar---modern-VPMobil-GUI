import time
import requests
from datetime import date
import sys
from pathlib import Path
import os

sys.path.append(str(Path(__file__).resolve().parent.parent))
import vp_data
from vpmobil.api import ResourceNotFound, Unauthorized


def send_notify(user: str = "all"):
    vp_data.log(f"Send notification to {user}")
    url = os.getenv("NTFY_PUBLIC_URL", "https://ntfy.sh").rstrip("/")
    topic = os.getenv("NTFY_TOPIC", "vprintfy")
    requests.post(
        f"{url}/{topic}", data="Neuer Plan verfügbar!",
        headers={"Priority": "normal", "Title": "VPrintfy", "Tags": "calendar"},
        timeout=10,
    ).raise_for_status()


def look_for_update(selected_date: date):
    """Lädt den Plan, aktualisiert den Cache bei neueren Daten und nutzt sonst den alten Cache."""

    vp_data.cleanup_cache()

    cached_plan = vp_data.load_plan_from_cache(selected_date)

    try:
        remote_plan = vp_data.fetch_plan_from_vpmobil(selected_date)
    except ResourceNotFound:
        if cached_plan is not None:
            vp_data.log(f"VpMobil hat keinen Plan für {selected_date.isoformat()} geliefert. Nutze vorhandenen Cache.")
            return

        vp_data.log(f"VpMobil hat keinen Plan für {selected_date.isoformat()} geliefert und es gibt keinen Cache.")
        raise
    except Unauthorized:
        vp_data.log("VpMobil-Zugriff verweigert. Zugangsdaten prüfen.")
        raise
    except Exception as error:
        if cached_plan is not None:
            vp_data.log(f"VpMobil konnte nicht erreicht werden ({error}). Nutze vorhandenen Cache.")
            return

        vp_data.log(f"VpMobil konnte nicht erreicht werden ({error}) und es gibt keinen Cache.")
        raise

    if vp_data.is_remote_plan_newer(cached_plan, remote_plan):
        vp_data.log(f"Neuerer Plan für {selected_date.isoformat()} gefunden. Cache wird aktualisiert.")
        vp_data.save_plan_to_cache(selected_date, remote_plan)
        send_notify() # Sendet an alle, die den Ntfy-Topic abonniert haben
        return

    vp_data.log(f"Kein neuerer Plan für {selected_date.isoformat()} gefunden. Nutze vorhandenen Cache.")
    return


if __name__ == "__main__":
    while True:
        look_for_update(date.today())
        time.sleep(int(os.getenv("NOTIFICATION_INTERVAL_SECONDS", "60")))