#!/bin/sh
# Render the configuration from environment variables, then run Asterisk in the foreground.
set -eu
: "${PHONE_USER:?set PHONE_USER (the SIP user name your phone app logs in with)}"
: "${PHONE_PASSWORD:?set PHONE_PASSWORD (a long random SIP password)}"
case "${#PHONE_PASSWORD}" in [0-9]|1[0-5]) echo "PHONE_PASSWORD must be at least 16 characters" >&2; exit 1;; esac
export GATEWAY_ADDRESS="${GATEWAY_ADDRESS:-gateway:9092}"
export SECRETARY_EXTENSION="${SECRETARY_EXTENSION:-100}"
# The address the phone reaches us at (e.g. this host's tailnet IP). Leave empty on a flat network.
if [ -n "${EXTERNAL_ADDRESS:-}" ]; then
  export EXTERNAL_LINES="external_media_address=${EXTERNAL_ADDRESS}
external_signaling_address=${EXTERNAL_ADDRESS}
local_net=${LOCAL_NET:-172.16.0.0/12}"
# LOCAL_NET must be only the container network. If it also covers the phone's network
# (a 172.16-31.x Wi-Fi, for example), Asterisk would advertise its private container address.
else
  export EXTERNAL_LINES=""
fi
# Optional second way in, e.g. the same Wi-Fi: SIP on port 5062 inside the container, advertised
# with the LAN address so the phone's audio comes back the right way.
if [ -n "${LAN_ADDRESS:-}" ]; then
  export LAN_TRANSPORT="[transport-lan]
type = transport
protocol = udp
bind = 0.0.0.0:5062
external_media_address = ${LAN_ADDRESS}
external_signaling_address = ${LAN_ADDRESS}
external_signaling_port = 5060
local_net = ${LOCAL_NET:-172.16.0.0/12}"
else
  export LAN_TRANSPORT=""
fi
for template in /etc/asterisk-templates/*; do
  envsubst '$PHONE_USER $PHONE_PASSWORD $GATEWAY_ADDRESS $SECRETARY_EXTENSION $EXTERNAL_LINES $LAN_TRANSPORT' < "$template" > "/etc/asterisk/$(basename "$template")"
done
chown -R asterisk:asterisk /etc/asterisk
chmod 600 /etc/asterisk/pjsip.conf
exec asterisk -f -U asterisk -G asterisk -vvv
