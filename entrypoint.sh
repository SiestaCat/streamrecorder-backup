#!/bin/sh
set -e

if [ "${ENABLE_PORT_FORWARD:-0}" = "1" ]; then
    iptables -t nat -A PREROUTING \
        -p tcp \
        --dport 9000 \
        -j DNAT \
        --to-destination 127.0.0.1:8085
fi

exec "$@"