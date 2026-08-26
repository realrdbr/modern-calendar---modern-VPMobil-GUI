# Reverse-proxy templates

These templates expose three separate HTTPS hostnames:

- `cal11.de` -> Kalender (Node/React) on `app:3000`
- `vp.cal11.de` -> Vertretungsplan (Python) on `vp:8000`
- `notify.cal11.de` -> ntfy on `ntfy:80`

Replace the example hostnames and certificate settings first. Set these values in `.env`:

```dotenv
CALENDAR_PUBLIC_URL=https://cal11.de
VERTRETUNGSPLAN_PUBLIC_URL=https://vp.cal11.de
NTFY_PUBLIC_URL=https://notify.cal11.de
NTFY_INTERNAL_URL=http://ntfy
COOKIE_SECURE=true
COOKIE_DOMAIN=cal11.de
```

`COOKIE_DOMAIN` wird für die dokumentierte Kombination `cal11.de` und
`vp.cal11.de` automatisch aus den öffentlichen URLs erkannt. Die explizite
Angabe bleibt für abweichende Domainstrukturen empfohlen. Der Wert darf nur
die gemeinsame Eltern-Domain enthalten; der Provisionierungsdienst und ntfy
benötigen diese Session-Cookies nicht.

For ntfy, keep `behind-proxy: true` in `ntfy/server.yml` when the proxy is the public entry point. The proxy must pass WebSocket upgrades and must not buffer ntfy streaming responses. Do not expose port 8090 publicly; the Compose template binds it to localhost.

For nginx, the calendar vhost in [nginx.conf](/home/rdbr/PycharmProjects/jahrgangskalender/proxy/nginx.conf) already includes upload-safe settings for `/api/upload` (`client_max_body_size 12m`, `proxy_request_buffering off`). Keep this when adjusting templates, otherwise uploads can fail with HTTP 413.

The application backend intentionally does not trust incoming `X-Forwarded-For`. The application should therefore be reached directly from the reverse proxy on localhost, or its trusted proxy handling must be designed and implemented before changing that behavior.

The ntfy iOS flow still requires `upstream-base-url: "https://ntfy.sh"` in `ntfy/server.yml`, plus the user's ntfy credentials in the mobile app. The topic link opens the web view, but it does not carry authentication.

Sources:

- https://docs.ntfy.sh/config/#behind-a-proxy-tls-etc
- https://docs.ntfy.sh/config/#ios-instant-notifications
- https://nginx.org/en/docs/http/websocket.html
- https://httpd.apache.org/docs/2.4/mod/mod_proxy.html
- https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
