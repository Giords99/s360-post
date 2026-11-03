# S360 – pubblicazione automatica su Instagram

Questo repository contiene **solo** i post di [@s360.data](https://www.instagram.com/s360.data/) già approvati e lo script che li pubblica.

## Come funziona

1. Un post approvato va in `post/<data>_<tema>/`, con le slide in JPEG (`01.jpg`, `02.jpg`, …) e il file `caption.txt`.
2. In `calendario.json` si aggiunge una riga con data, ora e cartella, e lo stato `programmato`.
3. Dal lunedì al venerdì, tra le 11 e le 15, GitHub esegue `pubblica.py` ogni 30 minuti. Se un post è arrivato all'ora prevista, lo script lo pubblica e lo segna come `pubblicato`, aggiungendo il link.
4. Se qualcosa va storto, lo stato diventa `errore` (o `saltato` se il controllo arriva con più di 6 ore di ritardo) e GitHub manda una mail.

## Prova a secco

Nella scheda **Actions** → **Pubblica su Instagram** → **Run workflow**, con la cartella `post/_prova`: lo script verifica il token e fa controllare le immagini a Instagram **senza pubblicare niente**.

## Segreti

- `IG_TOKEN`: token di accesso di Instagram (app Meta "S360 Publisher"). Scade dopo 60 giorni: da rinnovare.
