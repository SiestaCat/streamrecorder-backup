FROM alpine:3

RUN apk add --no-cache iptables python3 curl ca-certificates rclone

COPY script.py /script.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
RUN mkdir -p /root/streamrecorder
RUN touch /root/streamrecorder/cookie

EXPOSE 9000

ENTRYPOINT ["/entrypoint.sh"]