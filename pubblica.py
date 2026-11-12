"""S360 - pubblicazione automatica dei carousel su Instagram.

Uso:
  python pubblica.py                 pubblica i post del calendario arrivati all'ora prevista
  python pubblica.py --prova CARTELLA  prova a secco: verifica token e file, NON pubblica

Una cartella con reel.mp4 (e facoltativa copertina.jpg) diventa un reel; altrimenti le slide JPEG diventano un carousel.

Variabili d'ambiente: IG_TOKEN (segreto del repository), IG_USER_ID.
API usata: Instagram API with Instagram Login (graph.instagram.com).
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://graph.instagram.com/v23.0"
API_FB = "https://graph.facebook.com/v23.0"
ROMA = ZoneInfo("Europe/Rome")
RADICE = pathlib.Path(__file__).resolve().parent
CALENDARIO = RADICE / "calendario.json"
MAX_RITARDO = dt.timedelta(hours=6)  # oltre questo ritardo il post NON esce da solo
ATTESA_STORIA = dt.timedelta(minutes=75)  # tempo lasciato per la storia fatta a mano


class ErroreS360(Exception):
    pass


# ---------- chiamate all'API ----------

def api(metodo, percorso, **parametri):
    parametri["access_token"] = os.environ["IG_TOKEN"]
    dati = urllib.parse.urlencode(parametri)
    # Token di Instagram Login (IG...) -> graph.instagram.com
    # Token Meta/Facebook, es. utente di sistema del portfolio (EAA...) -> graph.facebook.com
    base = API_FB if parametri["access_token"].startswith("EAA") else API
    if os.environ.get("IG_API_BASE"):  # forzato per il token dei reel (vedi pubblica_reel)
        base = os.environ["IG_API_BASE"]
    url = f"{base}/{percorso}"
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
            err = json.loads(corpo)["error"]
            msg = err.get("message", corpo)
            # dettagli che servono a capire errori generici come "Fatal" (es. subcode 2207082 = video non ancora pronto)
            det = [f"{k}={err[k]}" for k in ("code", "error_subcode", "error_user_title", "error_user_msg", "fbtrace_id") if err.get(k)]
            if det:
                msg += " [" + "; ".join(str(d) for d in det) + "]"
        except Exception:
            msg = corpo
        raise ErroreS360(f"Instagram ha risposto {e.code} su {percorso}: {msg}") from None


def verifica_token():
    grezzo = os.environ.get("IG_TOKEN", "")
    tok = grezzo.strip().strip('"').strip("'").strip()
    os.environ["IG_TOKEN"] = tok
    # controllo senza rivelare il token: lunghezza, inizio e caratteri strani
    strani = sorted({c for c in tok if not (c.isalnum() or c in "-_.")})
    print(f"Controllo token: {len(tok)} caratteri, inizia con '{tok[:4]}', "
          f"spazi/a capo tolti: {len(grezzo) - len(tok)}, caratteri insoliti: {strani or 'nessuno'}")
    if not tok:
        raise ErroreS360("Il segreto IG_TOKEN è vuoto o non esiste")
    # lettura dell'account Instagram: funziona con entrambi i tipi di token
    atteso = os.environ["IG_USER_ID"]
    io = api("GET", atteso, fields="username")
    if io.get("username") != "s360.data":
        raise ErroreS360(f"Il token legge l'account @{io.get('username')}, non @s360.data")
    print(f"Token valido: può leggere @{io.get('username')} (ID {atteso}), "
          f"via {'graph.facebook.com' if tok.startswith('EAA') else 'graph.instagram.com'}")
    return io


def url_pubblico(file):
    """Indirizzo pubblico del file su GitHub, legato al commit esatto (niente cache vecchie)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "Giords99/s360-post")
    rif = os.environ.get("GITHUB_SHA") or "main"
    rel = file.relative_to(RADICE).as_posix()
    return f"https://raw.githubusercontent.com/{repo}/{rif}/{urllib.parse.quote(rel)}"


# ---------- controlli sul post ----------

def leggi_caption(cartella):
    f_caption = cartella / "caption.txt"
    if not f_caption.exists():
        raise ErroreS360("Manca caption.txt")
    caption = f_caption.read_text(encoding="utf-8").strip()
    if len(caption) > 2200:
        raise ErroreS360(f"Caption troppo lunga: {len(caption)} caratteri (max 2200)")
    if caption.count("#") > 30:
        raise ErroreS360("Più di 30 hashtag nella caption")
    return caption


def cartella_di(cartella):
    c = (RADICE / cartella).resolve()
    if not c.is_dir():
        raise ErroreS360(f"Cartella non trovata: {c}")
    return c


def leggi_post(cartella):
    cartella = cartella_di(cartella)
    slide = sorted(p for p in cartella.iterdir() if p.suffix.lower() in (".jpg", ".jpeg") and p.name not in ("copertina.jpg", "storia.jpg"))
    png = [p.name for p in cartella.iterdir() if p.suffix.lower() == ".png"]
    if png:
        raise ErroreS360(f"Instagram accetta solo JPEG: convertire {png}")
    if not 2 <= len(slide) <= 10:
        raise ErroreS360(f"Un carousel deve avere da 2 a 10 slide, qui sono {len(slide)}")
    caption = leggi_caption(cartella)
    print(f"{cartella.name}: {len(slide)} slide, caption di {len(caption)} caratteri")
    return slide, caption


# ---------- creazione e pubblicazione ----------

def attendi_pronto(id_container, nome, tentativi=30):
    for _ in range(tentativi):  # 30 tentativi = ~5 minuti
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


def controlla_video(video):
    """Controlli di base con ffprobe (se c'è): durata 3 s - 15 min, verticale, H.264."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name,width,height:format=duration", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=60)
        info = json.loads(out.stdout)
    except Exception:
        print("  (ffprobe non disponibile: salto i controlli sul video)")
        return
    s = info["streams"][0]
    durata = float(info["format"]["duration"])
    print(f"  video: {s['width']}x{s['height']}, {s['codec_name']}, {durata:.1f} s, {video.stat().st_size / 1e6:.1f} MB")
    if not 3 <= durata <= 900:
        raise ErroreS360(f"Il reel dura {durata:.1f} s: deve stare tra 3 secondi e 15 minuti")
    if s["codec_name"] != "h264":
        raise ErroreS360(f"Il reel deve essere H.264, è {s['codec_name']}")
    if s["width"] >= s["height"]:
        raise ErroreS360("Il reel deve essere verticale (9:16)")
    if video.stat().st_size > 300e6:
        raise ErroreS360("Il reel supera i 300 MB")


def carica_video(id_container, uri, video):
    """Caricamento diretto del file (upload 'resumable' di Meta): non dipende da GitHub come host del video."""
    dati = video.read_bytes()
    req = urllib.request.Request(uri, data=dati, method="POST", headers={
        "Authorization": f"OAuth {os.environ['IG_TOKEN']}", "offset": "0", "file_size": str(len(dati))})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            esito = json.load(r)
    except urllib.error.HTTPError as e:
        raise ErroreS360(f"Caricamento del video rifiutato ({e.code}): {e.read().decode(errors='replace')[:300]}") from None
    if not esito.get("success", True):
        raise ErroreS360(f"Caricamento del video non riuscito: {esito}")


# Varianti di caricamento del reel, provate in ordine se media_publish risponde con un errore
# (10/11: "400 Fatal" ripetuto con il caricamento diretto + copertina).
# (modo di caricamento, copertina, share_to_feed)
VARIANTI_REEL = [("diretto", True, True), ("diretto", False, False)]


def prepara_reel(cartella, modo="diretto", con_copertina=True, nel_feed=True):
    """Crea su Instagram il reel (non ancora pubblicato) e restituisce l'ID del container."""
    c = cartella_di(cartella)
    video = c / "reel.mp4"
    caption = leggi_caption(c)
    print(f"{c.name}: reel, caption di {len(caption)} caratteri")
    controlla_video(video)
    utente = os.environ["IG_USER_ID"]
    extra = {"share_to_feed": "true"} if nel_feed else {}
    copertina = c / "copertina.jpg"
    if copertina.exists() and con_copertina:
        extra["cover_url"] = url_pubblico(copertina)
    print(f"  variante: caricamento {modo}, {'con' if 'cover_url' in extra else 'senza'} copertina, {'con' if nel_feed else 'senza'} share_to_feed")
    r = None
    if modo == "diretto":
        try:
            r = api("POST", f"{utente}/media", media_type="REELS", upload_type="resumable", caption=caption, **extra)
            carica_video(r["id"], r["uri"], video)
            print("  video caricato")
        except (ErroreS360, KeyError) as e:
            print(f"  caricamento diretto non riuscito ({e}); provo con il link al file su GitHub")
            r = None
    if r is None:
        r = api("POST", f"{utente}/media", media_type="REELS", video_url=url_pubblico(video), caption=caption, **extra)
    attendi_pronto(r["id"], "reel", tentativi=60)  # i video richiedono più tempo: fino a ~10 minuti
    print("  reel pronto")
    return r["id"]


def prepara(cartella):
    """Reel se nella cartella c'è reel.mp4, altrimenti carousel."""
    return prepara_reel(cartella) if (cartella_di(cartella) / "reel.mp4").exists() else prepara_carousel(cartella)


def prepara_storia(cartella):
    """Storia "Nuovo post" (storia.jpg, 1080x1920), se c'è nella cartella. Restituisce l'ID del container o None."""
    f = cartella_di(cartella) / "storia.jpg"
    if not f.exists():
        return None
    r = api("POST", f"{os.environ['IG_USER_ID']}/media", media_type="STORIES", image_url=url_pubblico(f))
    attendi_pronto(r["id"], "storia")
    print("  storia pronta")
    return r["id"]


def uscito_dopo(quando):
    """Ultimo contenuto del feed pubblicato dopo 'quando' (serve se media_publish dà errore ma pubblica lo stesso)."""
    r = api("GET", f"{os.environ['IG_USER_ID']}/media", fields="id,timestamp,permalink", limit="3")
    for x in r.get("data", []):
        if dt.datetime.fromisoformat(x["timestamp"].replace("+0000", "+00:00")) > quando:
            return x
    return None


def pubblica_container(id_container, tentativi=3, attese=(30, 30, 30)):
    """media_publish con controllo: a volte Instagram risponde con un errore (es. '400 Fatal', 10/11)
    anche se il contenuto è uscito. Prima di riprovare si guarda se è già sul profilo, così non esce due volte."""
    utente = os.environ["IG_USER_ID"]
    inizio = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    for n in range(1, tentativi + 1):
        try:
            r = api("POST", f"{utente}/media_publish", creation_id=id_container)
            info = api("GET", r["id"], fields="permalink")
            return r["id"], info.get("permalink", "")
        except ErroreS360 as e:
            print(f"  tentativo {n}: {e}")
            try:
                print(f"  stato del contenuto: {api('GET', id_container, fields='status_code,status')}")
            except ErroreS360:
                pass
            time.sleep(attese[min(n - 1, len(attese) - 1)])
            try:
                x = uscito_dopo(inizio)
            except ErroreS360:
                x = None
            if x:
                print("  ...ma il contenuto risulta pubblicato sul profilo")
                return x["id"], x.get("permalink", "")
            if n == tentativi:
                raise


# ---------- modalità ----------

def prova(cartella):
    print("PROVA A SECCO: nessun post verrà pubblicato.")
    verifica_token()
    prepara(cartella)
    prepara_storia(cartella)
    print("Prova riuscita: token, file e caption vanno bene. Nulla è stato pubblicato.")


def storie_dopo(quando):
    """Storie pubblicate sul profilo dopo 'quando' (le storie restano visibili 24 ore)."""
    r = api("GET", f"{os.environ['IG_USER_ID']}/stories", fields="id,timestamp")
    return [x for x in r.get("data", [])
            if dt.datetime.fromisoformat(x["timestamp"].replace("+0000", "+00:00")) > quando]


def gestisci_storie(voci, adesso):
    """Storie di riserva: per i post pubblicati da almeno ATTESA_STORIA senza una storia fatta a mano."""
    for v in voci:
        if v.get("stato") != "pubblicato" or v.get("storia") != "in attesa":
            continue
        uscito = dt.datetime.fromisoformat(v["pubblicato_il"])
        if adesso - uscito < ATTESA_STORIA:
            continue
        if adesso - uscito > dt.timedelta(hours=20):
            v["storia"] = "non pubblicata: troppo tardi"
            continue
        try:
            if storie_dopo(uscito):
                v["storia"] = "fatta a mano"
                print(f"{v['cartella']}: c'è già una storia dopo il post, non ne pubblico un'altra")
                continue
            id_s = prepara_storia(v["cartella"])
            pubblica_container(id_s, tentativi=1)  # le storie non sono nel feed: niente controllo, niente nuovo tentativo
            v["storia"] = "pubblicata in automatico"
            print(f"{v['cartella']}: storia di riserva pubblicata")
        except ErroreS360 as e:
            v["storia"] = f"non pubblicata: {e}"
            print(f"{v['cartella']}: storia non pubblicata ({e})")


def pubblica_reel(cartella):
    """Reel: se c'è il segreto IG_TOKEN_REEL (token di Instagram Login, graph.instagram.com) si usa quello,
    perché con il token dell'utente di sistema (graph.facebook.com, account senza Pagina Facebook)
    media_publish dei reel risponde sempre 2207085 (10-12/11). Poi si torna al token normale."""
    tok_reel = os.environ.get("IG_TOKEN_REEL", "").strip().strip('"').strip("'").strip()
    if not tok_reel:
        return _pubblica_reel(cartella)
    salvati = os.environ["IG_TOKEN"], os.environ["IG_USER_ID"]
    os.environ["IG_TOKEN"] = tok_reel
    try:
        # il token generato con l'accesso Instagram (app con login unificato) inizia con EAA:
        # si prova prima graph.instagram.com, poi graph.facebook.com
        io = None
        for base in (API, API_FB):
            os.environ["IG_API_BASE"] = base
            try:
                if base == API:
                    io = api("GET", "me", fields="user_id,username")
                else:  # su graph.facebook.com /me è il profilo Facebook: si legge direttamente l'account Instagram
                    io = api("GET", salvati[1], fields="username")
                    io["user_id"] = salvati[1]
                print(f"  token dei reel accettato da {base}")
                break
            except ErroreS360 as e:
                print(f"  token dei reel rifiutato da {base}: {e}")
        if io is None:
            raise ErroreS360("IG_TOKEN_REEL non è accettato né da graph.instagram.com né da graph.facebook.com")
        if "user_id" not in io:
            io.update(api("GET", "me", fields="id,username"))
        if io.get("username") != "s360.data":
            raise ErroreS360(f"IG_TOKEN_REEL legge l'account @{io.get('username')}, non @s360.data")
        os.environ["IG_USER_ID"] = str(io.get("user_id") or io.get("id"))
        print(f"  reel con il token di Instagram Login (@{io['username']}, ID {os.environ['IG_USER_ID']}, inizia con '{tok_reel[:4]}')")
        return _pubblica_reel(cartella)
    finally:
        os.environ["IG_TOKEN"], os.environ["IG_USER_ID"] = salvati
        os.environ.pop("IG_API_BASE", None)


def _pubblica_reel(cartella):
    """Prova le varianti di VARIANTI_REEL finché una va; prima di ogni nuova variante controlla che il reel non sia già uscito."""
    try:
        lim = api("GET", f"{os.environ['IG_USER_ID']}/content_publishing_limit", fields="config,quota_usage")
        print(f"  limite di pubblicazione: {lim.get('data')}")
    except ErroreS360 as e:
        print(f"  (limite di pubblicazione non leggibile: {e})")
    inizio = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    ultimo = None
    for modo, cop, feed in VARIANTI_REEL:
        x = uscito_dopo(inizio)
        if x:
            print("  il reel risulta già pubblicato sul profilo")
            return x["id"], x.get("permalink", "")
        try:
            id_c = prepara_reel(cartella, modo, cop, feed)
            # anche con status FINISHED la versione del video per la riproduzione può non essere pronta
            # (errore 2207082 "progressive_video_not_ready"): si aspetta e si riprova con attese crescenti
            time.sleep(60)
            return pubblica_container(id_c, tentativi=2, attese=(60, 60))
        except ErroreS360 as e:
            print(f"  variante non riuscita: {e}")
            ultimo = e
    raise ultimo


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
            if (cartella_di(v["cartella"]) / "reel.mp4").exists():
                id_media, link = pubblica_reel(v["cartella"])
            else:
                id_c = prepara(v["cartella"])
                id_media, link = pubblica_container(id_c)
            v.update(stato="pubblicato", pubblicato_il=dt.datetime.now(ROMA).isoformat(timespec="minutes"),
                     id_media=id_media, link=link)
            print(f"  PUBBLICATO: {link}")
            # storia "Nuovo post": non subito. Si lascia il tempo all'utente di condividere il post a mano
            # (storia toccabile); se dopo ATTESA_STORIA non c'è nessuna storia, la pubblica lo script.
            if (cartella_di(v["cartella"]) / "storia.jpg").exists():
                v["storia"] = "in attesa"
        except ErroreS360 as e:
            v.update(stato="errore", nota=str(e))
            errori.append(f"{v['cartella']}: {e}")
        # salva subito, così un post non può uscire due volte
        CALENDARIO.write_text(json.dumps(voci, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if any(v.get("storia") == "in attesa" for v in voci):
        verifica_token()
        gestisci_storie(voci, dt.datetime.now(ROMA))
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
