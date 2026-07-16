#!/bin/sh
# Runs inside the nginx container at startup (mounted into /docker-entrypoint.d/).
# Enables the HTTPS server block ONLY when real certs are present, so the stack still
# starts on HTTP where there are none (local dev, fresh clone, before certbot runs).
set -e
mkdir -p /etc/nginx/tls-enabled
if [ -s /etc/nginx/certs/fullchain.pem ] && [ -s /etc/nginx/certs/privkey.pem ]; then
    cp /etc/nginx/tls-available/ssl.conf /etc/nginx/tls-enabled/ssl.conf
    echo "enable-tls: certs found -> HTTPS (443) enabled"
else
    rm -f /etc/nginx/tls-enabled/ssl.conf
    echo "enable-tls: no certs in /etc/nginx/certs -> HTTP only (this is expected locally)"
fi
