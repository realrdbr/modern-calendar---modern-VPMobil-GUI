# cal11 Fusion-Stack (Kalender + Vertretungsplan + ntfy)

Dieses Repository betreibt beide Anwendungen gemeinsam:

- Kalender (TypeScript/React + Node)
- Vertretungsplan inkl. Ankündigungen (Python)
- gemeinsame MariaDB
- ntfy für Push/Abos

Die Python-App nutzt dieselbe Datenbank wie der Kalender. VP-spezifische Tabellen liegen als `vp_*` in derselben DB, und Kalender-Logins (Benutzername/PIN) werden für VP mitgenutzt.

## 1) Voraussetzungen

- Docker + Docker Compose Plugin
- Git
- Für lokale Admin-Aufrufe optional: Python 3.11+

## 2) Projekt holen

```bash
git clone <DEIN-REPO-URL> jahrgangskalender
cd jahrgangskalender
```

## 3) `.env` anlegen

```bash
cp .env.example .env
```

Dann in `.env` mindestens setzen:

- `DB_PASSWORD`
- `DB_ROOT_PASSWORD`
- `APP_ENCRYPTION_KEY`
- `SCHULNUMMER`
- `BENUTZERNAME`
- `PASSWORT`

Den Encryption-Key erzeugst du so:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 4) Lokal testen (ohne Reverse Proxy)

ggf.  python Abhängigkeiten installieren:
```bash
pip install -r requirements.txt
```
```bash
./start-all.sh docker
```

Danach erreichbar:

- Kalender: `http://127.0.0.1:${CAL11_PORT}`
- Vertretungsplan: `http://127.0.0.1:${VP_PORT}`
- ntfy: `http://127.0.0.1:${NTFY_PORT}`

Hinweise:

- Caddy startet dabei **nicht** automatisch.
- `./start-all.sh` synchronisiert beim Start automatisch ntfy-Logins aus der VP-Datenbank (`sync-ntfy-users.sh`).

## 5) Nutzer/Benachrichtigungen verwalten

Beispiele:

```bash
python admin.py create-user lisa --class 11
python admin.py send-test lisa --type morning
python admin.py send-test lisa --type change
python admin.py send-test lisa --type next --block 1
```

Wenn du `python admin.py ...` auf dem Host ausführst, wird bei Docker/MariaDB-Setup automatisch in den `vp`-Container delegiert.

## 6) Produktion mit Let’s Encrypt + Reverse Proxy (Caddy)

### DNS vorbereiten

Lege A/AAAA-Records auf deinen Server:

- `cal11.de`
- `vp.cal11.de`
- `notify.cal11.de`

### Firewall/Ports

- Port `80/tcp` offen (ACME HTTP-Challenge)
- Port `443/tcp` offen (HTTPS)

### `.env` für Produktion setzen

Mindestens:

```dotenv
CALENDAR_PUBLIC_URL=https://cal11.de
VERTRETUNGSPLAN_PUBLIC_URL=https://vp.cal11.de
NTFY_PUBLIC_URL=https://notify.cal11.de
NTFY_INTERNAL_URL=http://ntfy
TLS_EMAIL=deine-mail@beispiel.de
COOKIE_SECURE=true
NTFY_BEHIND_PROXY=true
```

### Caddy-Konfiguration prüfen

Die Domain-Zuordnung steht in [Caddyfile](/home/rdbr/PycharmProjects/jahrgangskalender/proxy/Caddyfile).  
Wenn du andere Domains nutzt, passe sie dort an.

### Mit Proxy starten (automatisch)

```bash
./start-all.sh docker-proxy
```

Caddy holt Zertifikate automatisch via Let’s Encrypt. Beim ersten Start kann das 1-2 Minuten dauern.
Bei einem anderen Proxy, musst du das Template nutzen und mit ./start-all.sh docker starten

Prüfen:

```bash
curl -I https://cal11.de
curl -I https://vp.cal11.de
curl -I https://notify.cal11.de
```

## 7) Alternative Proxy-Templates

Neben Caddy sind Templates für weitere Proxies vorhanden:

- [proxy/](/home/rdbr/PycharmProjects/jahrgangskalender/proxy/)
- Details in [README.md](/home/rdbr/PycharmProjects/jahrgangskalender/proxy/README.md)

## 8) Sicherheitsrelevante Defaults

- PINs werden gehasht (Argon2id im VP-Teil; sichere Prüfung im Kalender-Teil).
- Login-Versuche werden begrenzt (Brute-Force-Schutz).
- Sessions/CSRF serverseitig.
- ntfy-Zugangsdaten sind verschlüsselt in der DB.
- Für öffentliche Instanzen immer HTTPS + `COOKIE_SECURE=true`.

## 9) Tests

```bash
python -m unittest tests.test_accounts_and_subscriptions
npm run lint
npm run build
```
