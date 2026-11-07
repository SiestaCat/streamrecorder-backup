FROM alpine:3

RUN apk add --no-cache iptables python3 curl ca-certificates rclone

COPY script.py /script.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 9000

ENTRYPOINT ["/entrypoint.sh"]