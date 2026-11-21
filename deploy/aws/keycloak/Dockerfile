# Keycloak for AWS: PostgreSQL, health endpoints and the /auth path are built in,
# so the container starts with `start --optimized`. The realm template has no users
# or secrets; bootstrap.sh applies them per environment (see make_realm.py).
FROM quay.io/keycloak/keycloak:26.3 AS builder
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=false \
    KC_HTTP_RELATIVE_PATH=/auth
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.3
COPY --from=builder /opt/keycloak/ /opt/keycloak/
COPY --chown=keycloak:root --chmod=0644 realm-baryon-ax.aws.json /opt/keycloak/baryon/realm.json
COPY --chown=keycloak:root --chmod=0755 bootstrap.sh /opt/keycloak/baryon/bootstrap.sh
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized"]
