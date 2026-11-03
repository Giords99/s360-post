"""S360 - pubblicazione automatica dei carousel su Instagram.

Uso:
  python pubblica.py                 pubblica i post del calendario arrivati all'ora prevista
  python pubblica.py --prova CARTELLA  prova a secco: verifica token e immagini, NON pubblica

Variabili d'ambiente: IG_TOKEN (segreto del repository), IG_USER_ID.
API usata: Instagram API with Instagram Login (graph.instagram.com).
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://graph.instagram.com/v23.0"
ROMA = ZoneInfo("Europe/Rome")
RADICE = pathlib.Path(__file__).resolve().parent
CALENDARIO = RADICE / "calendario.json"
MAX_RITARDO = dt.timedelta(hours=6)  # oltre questo ritardo il post NON esce da solo


class ErroreS360(Exception):
    pass


# ---------- chiamate all'API ----------

def api(metodo, percorso, **parametri):
    parametri["access_token"] = os.environ["IG_TOKEN"]
    dati = urllib.parse.urlencode(parametri)
    url = f"{API}/{percorso}"
    if metodo == "GET":
        richiesta = urllib.request.Request(f"{url}?{dati}")
    else:
        richiesta = urllib.request.Request(url, data=dati.encode(), method="POST")
    try:
        with urllib.request.urlopen(richiesta, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode(errors="replace")
        try:
            msg = json.loads(corpo)["error"].get("message", corpo)
        except Exception:
            msg = corpo
        raise ErroreS360(f"Instagram ha risposto {e.code} su {percorso}: {msg}") from None


def verifica_token():
    io = api("GET", "me", fields="user_id,username")
    atteso = os.environ.get("IG_USER_ID")
    if atteso and str(io.get("user_id")) != str(atteso):
        raise ErroreS360(f"Il token appartiene a @{io.get('username')} ({io.get('user_id')}), non all'account atteso {atteso}")
    print(f"Token valido per @{io.get('username')} (ID {io.get('user_id')})")
    return io


def url_pubblico(file):
    """Indirizzo pubblico del file su GitHub, legato al commit esatto (niente cache vecchie)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "Giords99/s360-post")
    rif = os.environ.get("GITHUB_SHA") or "main"
    rel = file.relative_to(RADICE).as_posix()
    return f"https://raw.githubusercontent.com/{repo}/{rif}/{urllib.parse.quote(rel)}"


# ---------- controlli sul post ----------

def leggi_post(cartella):
    cartella = (RADICE / cartella).resolve()
    if not cartella.is_dir():
        raise ErroreS360(f"Cartella non trovata: {cartella}")
    slide = sorted(p for p in cartella.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    png = [p.name for p in cartella.iterdir() if p.suffix.lower() == ".png"]
    if png:
        raise ErroreS360(f"Instagram accetta solo JPEG: convertire {png}")
    if not 2 <= len(slide) <= 10:
        raise ErroreS360(f"Un carousel deve avere da 2 a 10 slide, qui sono {len(slide)}")
    f_caption = cartella / "caption.txt"
    if not f_caption.exists():
        raise ErroreS360("Manca caption.txt")
    caption = f_caption.read_text(encoding="utf-8").strip()
    if len(caption) > 2200:
        raise ErroreS360(f"Caption troppo lunga: {len(caption)} caratteri (max 2200)")
    if caption.count("#") > 30:
        raise ErroreS360("Più di 30 hashtag nella caption")
    print(f"{cartella.name}: {len(slide)} slide, caption di {len(caption)} caratteri")
    return slide, caption


# ---------- creazione e pubblicazione ----------

def attendi_pronto(id_container, nome):
    for _ in range(30):  # fino a ~5 minuti
        stato = api("GET", id_container, fields="status_code,status").get("status_code")
        if stato == "FINISHED":
            return
        if stato in ("ERROR", "EXPIRED"):
            dettaglio = api("GET", id_container, fields="status").get("status")
            raise ErroreS360(f"{nome}: Instagram ha rifiutato il contenuto ({stato}: {dettaglio})")
        time.sleep(10)
    raise ErroreS360(f"{nome}: Instagram non ha finito di elaborare in tempo")


def prepara_carousel(cartella):
    """Crea su Instagram il carousel (non ancora pubblicato) e restituisce l'ID del container."""
    slide, caption = leggi_post(cartella)
    utente = os.environ["IG_USER_ID"]
    figli = []
    for i, f in enumerate(slide, 1):
        r = api("POST", f"{utente}/media", image_url=url_pubblico(f), is_carousel_item="true")
        attendi_pronto(r["id"], f"slide {i}")
        figli.append(r["id"])
        print(f"  slide {i}/{len(slide)} accettata ({f.name})")
    r = api("POST", f"{utente}/media", media_type="CAROUSEL", children=",".join(figli), caption=caption)
    attendi_pronto(r["id"], "carousel")
    print("  carousel pronto")
    return r["id"]


def pubblica_container(id_container):
    utente = os.environ["IG_USER_ID"]
    r = api("POST", f"{utente}/media_publish", creation_id=id_container)
    info = api("GET", r["id"], fields="permalink")
    return r["id"], info.get("permalink", "")


# ---------- modalità ----------

def prova(cartella):
    print("PROVA A SECCO: nessun post verrà pubblicato.")
    verifica_token()
    prepara_carousel(cartella)
    print("Prova riuscita: token, immagini e caption vanno bene. Nulla è stato pubblicato.")


def esegui_calendario():
    voci = json.loads(CALENDARIO.read_text(encoding="utf-8"))
    adesso = dt.datetime.now(ROMA)
    errori = []
    da_fare = []
    for v in voci:
        if v.get("stato") != "programmato":
            continue
        quando = dt.datetime.fromisoformat(f"{v['data']}T{v['ora']}").replace(tzinfo=ROMA)
        if quando > adesso:
            continue
        if adesso - quando > MAX_RITARDO:
            v["stato"] = "saltato"
            v["nota"] = f"non pubblicato: controllo arrivato alle {adesso:%d/%m %H:%M}, troppo tardi"
            errori.append(f"{v['cartella']}: saltato per ritardo, da pubblicare a mano o riprogrammare")
            continue
        da_fare.append(v)

    if not da_fare:
        print(f"{adesso:%d/%m/%Y %H:%M}: nessun post da pubblicare adesso.")
    else:
        verifica_token()
    for v in da_fare:
        print(f"Pubblico {v['cartella']} (previsto {v['data']} {v['ora']})")
        try:
            id_c = prepara_carousel(v["cartella"])
            id_media, link = pubblica_container(id_c)
            v.update(stato="pubblicato", pubblicato_il=dt.datetime.now(ROMA).isoformat(timespec="minutes"),
                     id_media=id_media, link=link)
            print(f"  PUBBLICATO: {link}")
        except ErroreS360 as e:
            v.update(stato="errore", nota=str(e))
            errori.append(f"{v['cartella']}: {e}")
        # salva subito, così un post non può uscire due volte
        CALENDARIO.write_text(json.dumps(voci, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    CALENDARIO.write_text(json.dumps(voci, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errori:
        raise ErroreS360("\n".join(errori))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prova", metavar="CARTELLA", help="prova a secco su una cartella, senza pubblicare")
    a = p.parse_args()
    try:
        prova(a.prova) if a.prova else esegui_calendario()
    except ErroreS360 as e:
        print(f"ERRORE: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
