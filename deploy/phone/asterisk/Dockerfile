# Asterisk for the phone gateway: one SIP account (your phone app) whose calls are
# answered and bridged to the voice secretary gateway over AudioSocket.
FROM alpine:3.20
RUN apk add --no-cache asterisk gettext \
    && rm -rf /etc/asterisk/* \
    && mkdir -p /var/run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk \
    && chown -R asterisk:asterisk /var/run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk /etc/asterisk
COPY config/ /etc/asterisk-templates/
COPY entrypoint.sh /entrypoint.sh
EXPOSE 5060/udp 10000-10019/udp
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
