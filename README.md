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
git clone https://github.com/realrdbr/modern-calendar---modern-VPMobil-GUI.git vpcal
cd vpcal
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
- In Notify können Nutzer mehrere Klassen, individuelle Stunden-Uhrzeiten sowie Kalender-Erinnerungen pro Kategorie konfigurieren.

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
NTFY_AUTH_DEFAULT_ACCESS=deny-all
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
- ntfy läuft standardmäßig mit `deny-all`; Nutzer dürfen nur ihr eigenes Topic lesen/schreiben, der Server nutzt einen separaten Systemzugang.
- Für öffentliche Instanzen immer HTTPS + `COOKIE_SECURE=true`.

## 9) Tests

```bash
python -m unittest tests.test_accounts_and_subscriptions
npm run lint
npm run build
```

### ntfy auf dem Production-Server: Konfiguration und Diagnose

Für `./start-all.sh docker-proxy` (Caddy) beziehungsweise `./start-all.sh docker`
(mit eigenem Reverse Proxy) gehören diese Werte in die `.env` im Projektverzeichnis:

```dotenv
NTFY_PUBLIC_URL=https://notify.cal11.de
NTFY_INTERNAL_URL=http://ntfy
NTFY_BEHIND_PROXY=true
NTFY_AUTH_DEFAULT_ACCESS=deny-all
NTFY_BIND_HOST=127.0.0.1
NTFY_PORT=8090
APP_TIMEZONE=Europe/Berlin
NOTIFICATION_INTERVAL_SECONDS=60
NTFY_LOG_LEVEL=INFO
NTFY_VISITOR_REQUEST_LIMIT_BURST=120
NTFY_VISITOR_REQUEST_LIMIT_REPLENISH=5s
NTFY_VISITOR_SUBSCRIPTION_LIMIT=250
COOKIE_SECURE=true
```

Die öffentliche URL muss vom Handy erreichbar sein und mit DNS, TLS und dem
Reverse Proxy übereinstimmen. `proxy/Caddyfile` enthält fest die Domains
`cal11.de`, `vp.cal11.de` und `notify.cal11.de`; andere Domains dort ebenfalls
anpassen. `TLS_EMAIL` für Caddy setzen. Ein Proxy auf dem Host leitet ntfy an
`127.0.0.1:8090` weiter, der mitgelieferte Caddy an `ntfy:80`. Der VP-Container
sendet über `http://ntfy-delivery` im dedizierten internen Netzwerk, unabhängig
vom öffentlichen Port. Compose setzt diesen Versandweg fest; `NTFY_INTERNAL_URL`
in der `.env` gilt nur für lokale Ausführung/Sonderinstallationen. Ohne Proxy
`NTFY_BEHIND_PROXY=false` verwenden und die öffentliche URL entsprechend setzen.

`APP_ENCRYPTION_KEY` muss der bestehende gültige Fernet-Schlüssel sein; nicht bei
jedem Start neu erzeugen, da damit gespeicherte Zugangsdaten entschlüsselt werden.
DB-Zugang, `ADMIN_PANEL_PASSWORD` und Schulzugang müssen ebenfalls stimmen.
Compose setzt `NTFY_PROVISIONER_URL`, `NTFY_PROVISIONER_SECRET` (aus dem
Verschlüsselungsschlüssel), Cache-/Auth-Dateien und `NTFY_AUTOSTART=false`
automatisch. `NTFY_COMPOSE_FILE` wird im vollständigen Docker-Stack nicht benötigt.
`NTFY_SERVER_USERNAME` und `NTFY_SERVER_PASSWORD` normalerweise weglassen: Der
Systemzugang wird automatisch abgeleitet und provisioniert. Persönliche Topics
und Passwörter kommen aus der Datenbank, nicht aus der `.env`.

Änderungen mit `docker compose up -d --build` (bei Caddy zusätzlich
`--profile proxy` vor `up`) übernehmen; ein bloßes `restart` übernimmt keine neuen
Container-Umgebungswerte. `start-all.sh` baut ebenfalls neu und synchronisiert
Zugänge, bleibt aber im Vordergrund: **Strg+C beendet den gesamten Stack**.
Für dauerhaften Betrieb stattdessen `docker compose up -d --build` und danach
`bash ./sync-ntfy-users.sh` verwenden. `start-all.sh local` startet nur Python und
Node auf dem Host und benötigt dafür eine passende lokale DB-/ntfy-Konfiguration.

```bash
docker compose logs --since=30m --tail=300 vp ntfy ntfy-provisioner
```

Die JSON-Logs enthalten UTC-Zeitstempel, beim Worker zusätzlich Ortszeit und
Zeitzone. `ntfy.request_failed` unterscheidet Authentifizierung (401), Topic-Rechte
(403), Rate-Limit (429), Verbindungsfehler und Timeouts; `operation` unterscheidet
persönlichen Test, Systemversand und Provisionierung. Ein erfolgreicher Test
verwendet den persönlichen Zugang, der automatische Versand den System-Publisher.
`ntfy.request_ok` bestätigt die Annahme durch ntfy, nicht die Anzeige am Handy.
Nachrichteninhalte, Topic-Pfade, Passwörter und vollständige Fehler-URLs werden
in diesen Diagnoseereignissen nicht protokolliert.

`ntfy.worker_started` zeigt die wirksame Konfiguration, `ntfy.worker_heartbeat`
alle 15 Minuten Planverfügbarkeit und Versandzahlen des aktuellen Durchlaufs.
`ntfy.worker_slow` zeigt eine Überschreitung des Intervalls. Für eine kurze
Fehlersuche `NTFY_LOG_LEVEL=DEBUG` setzen und VP neu erstellen: Dann erscheinen
pro Durchlauf die Uhrzeit und pro Nutzer-ID die Zeitplaneinstellungen.
Anschließend wieder `INFO` setzen. Die Docker-Logs von VP, ntfy und Provisioner
rotieren mit maximal drei Dateien à 10 MB pro Dienst. Es gibt keine zusätzlichen
Health-Polls oder Datenbankabfragen für das Logging.

Der Versand erfolgt im nächsten Durchlauf (Standard 60 Sekunden, Minimum 15),
bei langsamen Abrufen oder vielen Empfängern später. Die erste konfigurierte
Unterrichtszeit löst die Tagesübersicht aus; spätere Zeiten die nächste
Blockankündigung. Tagesübersichten werden am selben Tag nachgeholt; nächste
Blockankündigungen werden nach mehr als 20 Minuten ausgelassen. Diese Regeln
können ebenfalls erklären, warum eine Nachricht später kommt oder ausbleibt.


### Behebung von HTTP 429 beim ntfy-Versand

Der vollständige Docker-Stack nimmt ausschließlich die feste VP-Adresse im
separaten internen Netz vom ntfy-Request-Limit aus. Testversand, automatischer
Versand und Aufräumen verwenden dieses Netz. Öffentliche Clients und der Proxy
sind nicht ausgenommen; `deny-all` und persönliche Topic-Rechte bleiben wirksam.
Eine Erhöhung von `NTFY_VISITOR_SUBSCRIPTION_LIMIT` hilft bei Publish-429 nicht.
Die IP-Ausnahme bleibt auch nach Container-Neustarts gültig und benötigt keine
DNS-Auflösung des VP-Dienstes beim ntfy-Start.

Standardnetz: `172.30.251.0/29`, ntfy: `.2`, VP: `.3`. Falls dieses Subnetz bereits
verwendet wird, `NTFY_DELIVERY_SUBNET`, `NTFY_DELIVERY_SERVER_IP` und
`NTFY_DELIVERY_VP_IP` gemeinsam auf ein freies Subnetz mit zwei passenden Adressen
setzen. Proxy und weitere Dienste dürfen diesem Netz nicht beitreten. Dafür
sind sonst keine neuen `.env`-Werte nötig. `ntfy-compose.yml` allein beziehungsweise
`start-all.sh local` richtet dieses VP-Netz nicht ein.

Auf Production nach Übernahme der Dateien:

```bash
docker compose up -d --build ntfy vp
```

Auch `./start-all.sh docker` beziehungsweise `docker-proxy` übernimmt die Änderung.
Ein bloßes `restart` reicht nicht. Der Server-Test wiederholt einen vorübergehenden
429 einmal nach fünf Sekunden beziehungsweise nach `Retry-After` (höchstens zehn
Sekunden Wartezeit). Längere Limits und bekannte andere ntfy-Limits werden nicht
sofort wiederholt. Bleibt der 429 bestehen, erscheint eine passende Meldung statt
„Prüfe die Verbindung“. Hintergrundzustellungen bleiben beim Fehler unbestätigt
und werden im nächsten regulären Durchlauf erneut geprüft. Logs enthalten jetzt
auch den numerischen `ntfy_code`, etwa `42901` für das Request-Limit.

Verifiziert mit dem Image `binwiederhier/ntfy:v2.25.0`: 20 interne Nachrichten bei
Burst-Limit 3 erfolgreich; öffentliche Anfragen ab Nummer 4 mit 429 abgelehnt;
interner Versand danach weiterhin erfolgreich; fremde Topics und anonyme Zugriffe
mit 403 gesperrt. Die Ausnahme behebt das Request-Limit des eigenen Stacks;
externe Proxy-Limits, globale Servergrenzen und die Push-Zustellung ans Endgerät
sind davon unabhängig.

Zusätzliche Prüfung der Start- und Versandkette:

- Compose wartet vor dem Start von VP auf erfolgreiche Healthchecks von ntfy und
  Provisioner. Die Prüfungen laufen alle 30 Sekunden und lesen nur den lokalen
  Health-Endpunkt.
- `sync-ntfy-users.sh` synchronisiert im VP-Container über den signierten
  Provisioner. Es verwendet die DB-Startwiederholungen der Anwendung; DB- oder
  Provisionierungsfehler führen zu einem Fehlerstatus statt einem falschen Erfolg.
- Fehlgeschlagene Planänderungsnachrichten werden im nächsten Durchlauf erneut
  versucht. Bereits erfolgreich belieferte Nutzer bleiben persistent dedupliziert.
- `tests/ntfy_end_to_end.py` prüft mit künstlichen Daten echte Veröffentlichungen
  und liest die Nachrichten zurück: Kalender drei/einen Tag vorher mit getrennten
  Kategoriezeiten, Tages-/Vorabendübersicht, nächste Stunde, Deduplizierung und
  persönliche Testnachrichten. Es ist ein expliziter Integrationstest für einen
  isolierten Teststack (`NTFY_E2E=1`), kein Test gegen Production. Im Teststack
  müssen `ntfy-delivery`, `ntfy-public` (öffentlicher Netzwerkweg), der signierte
  Provisioner, eine frische Auth-/Cache-Datenbank und ein Burst-Limit von 3 mit
  Refill 1h konfiguriert sein; externe Push-Upstreams bleiben deaktiviert.

Diese Prüfungen bestätigen die korrekte Annahme und Speicherung durch ntfy.
Die Anzeige auf einem konkreten Handy erfordert zusätzlich das richtige Abo,
Netzverbindung und funktionierende Push-/Geräteeinstellungen. Hier wird keine
sekundengenaue Anzeige auf Endgeräten garantiert.

Auch nach vollständiger Neuerstellung der isolierten Testcontainer wurden erneut
20 persönliche Tests und eine Nachricht des System-Publishers angenommen; zuvor
veröffentlichte Nachrichten und Zugangsdaten blieben erhalten.

Wenn `start-all.sh` bereits beim Start oder bei der Zugangssynchronisierung
scheitert, bleiben die Container zur Diagnose erhalten. Das Skript gibt den
Containerstatus, die letzten Dienst-Logs und die ntfy-Healthcheck-Ergebnisse aus.
Nach einem erfolgreichen Start gilt weiterhin: Strg+C fährt den Stack herunter.


### Uploads und Proxy absichern (September 2026)

Kalenderdateien liegen dauerhaft im Host-Verzeichnis `uploads/`, eingebunden als
`/app/uploads`. Beim Upgrade über `start-all.sh` werden Dateien aus dem bisherigen
App-Container vor dessen Neuerstellung kopiert; bereits vorhandene Host-Dateien
bleiben erhalten. Beim ersten Upgrade deshalb `start-all.sh` benutzen, bevor der
alte App-Container entfernt wird. Bereits verlorene Dateien benötigen ein Backup
oder einen erneuten Upload. Das Verzeichnis gehört in die Datensicherung.
Fehlende Dateien liefern jetzt HTTP 404 statt der HTML-Startseite. Downloads
behalten ihre Dateinamen und werden ohne MIME-Sniffing ausgeliefert.

Für einen Proxy auf demselben Host in `.env` setzen:

```dotenv
APP_BIND_HOST=127.0.0.1
VP_BIND_HOST=127.0.0.1
NTFY_BIND_HOST=127.0.0.1
NTFY_BEHIND_PROXY=true
COOKIE_SECURE=true
```

Bestehende `.env`-Einträge überschreiben Compose-Defaults. Ein externer Proxy auf
einem anderen Host braucht gezielt freigegebene private Bind-Adressen/Firewallregeln.
`start-all.sh docker-proxy` aktiviert die ntfy-Proxy-Auswertung automatisch.
Beim Host-nginx bleibt es bei `start-all.sh docker` mit obigen Einstellungen.
Das nginx-Beispiel ersetzt eingehende Forwarded-For-Header durch die tatsächliche
Client-IP; andernfalls könnten Clients die IP-basierte Begrenzung manipulieren.
Die 15-MB-Proxygrenze berücksichtigt Base64 für maximal 10 MB Dateidaten.
Nach Änderungen nginx-Konfiguration mit `nginx -t` prüfen und neu laden.

`.env`, Datenbanken, Uploads und lokale Python-Umgebungen werden aus dem
Docker-Build-Kontext ausgeschlossen. Bereits gebaute alte Images werden dadurch
nicht nachträglich bereinigt. Lokale `.env`-Dateirechte: `chmod 600 .env`.

HTTP 429 allein belegt keinen Angriff oder Passwortdiebstahl. Für die Ursache
werden ntfy-Fehlercode und Proxy-/VP-Protokolle vom betroffenen Zeitpunkt benötigt.
Ein DNS-Fehler beim Upstream `ntfy.sh` kann zusätzlich iOS-Push verhindern, obwohl
der eigene ntfy-Server die Nachricht angenommen hat.

Den Versand samt Zugangsschutz reproduzierbar ohne Produktionskonten prüfen:

```bash
docker compose -p ntfy-access-e2e -f tests/ntfy-compose.e2e.yml up -d --build
docker compose -p ntfy-access-e2e -f tests/ntfy-compose.e2e.yml exec -T vp python tests/ntfy_end_to_end.py
docker compose -p ntfy-access-e2e -f tests/ntfy-compose.e2e.yml down -v
```

Der Teststack besitzt nur interne Netze, keine veröffentlichten Ports und eigene
Testdaten. Der letzte Befehl entfernt ausschließlich seine Testdaten; vor einer
Wiederholung ist diese Bereinigung erforderlich. Das Subnetz `172.30.250.0/29`
muss frei sein. Geprüft werden die tatsächlichen TCP-Adressen des internen
Versandwegs, Zeitsteuerung, Deduplizierung, Versand bei erschöpftem öffentlichen
Request-Limit, angemeldete JSON-/SSE-Abos über nginx, erneute Provisionierung,
Topic-Isolation und das Leseverbot des Server-Publishers. Dabei ist die öffentliche
URL auf `https://notify.cal11.de` gesetzt; der Versand bleibt trotzdem intern.
Der Test läuft aus dem neu gebauten VP-Image, ohne eingebundenen Quellcode.

Loopback-Portfreigaben beschränken nur direkte Zugriffe auf die Host-Ports.
Der Caddy-Container erreicht die Dienste weiterhin über `cal11_net`; nginx auf
demselben Host erreicht sie über `127.0.0.1`. Die Anmeldung, Schlüssel und
persönlichen ntfy-Zugangsdaten werden durch diese Änderung nicht ersetzt.
Öffentliche Health-/Login-Prüfungen beweisen keine interne Produktionsroute
und keine Push-Anzeige auf einem bestimmten Endgerät.
