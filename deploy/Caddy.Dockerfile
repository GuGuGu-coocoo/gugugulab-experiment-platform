# Keep the Caddy feature release, rebuild its HTTP/TLS implementation with
# patched Go and x/net versions. No application source or data is copied.
FROM caddy:2.11.7-builder@sha256:c2fc374fd352439d76e1777b28d97d9f6d9c1dd86116db9227460374d46097d5 AS builder
ENV GOTOOLCHAIN=go1.26.10
RUN xcaddy build v2.11.7 \
    --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
    --output /usr/bin/caddy

FROM caddy:2.11.7@sha256:504a44d7d74bd2bc28a66afc4774912fc21223ca8a67734af1c54ab2f999b489
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
