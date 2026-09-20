# ModDex hosted pitch deployment

This package contains no Google credentials. Never commit `credentials.json`.

Required hosting secrets:

- `MODDEX_PIN`: staff password protecting the entire app.
- `MODDEX_DAILY_CAP`: recommended `30` for a pitch.
- Choose one image engine:
  - AI Studio: `MODDEX_API_KEY`
  - Vertex: `MODDEX_VERTEX_CREDENTIALS` containing the service-account JSON, or
    `MODDEX_VERTEX_CREDENTIALS_B64` containing base64 of that JSON.

The container reads the host-provided `PORT`, binds to all interfaces and exposes
`/healthz` for platform health checks. All other routes require the staff PIN.

Koyeb free demo settings:

- Builder: Dockerfile
- Instance: Free
- Exposed port: 7860 (the platform may override this with `PORT`)
- Health check: HTTP `/healthz`
- Region: Frankfurt or Washington, D.C.

Use the generated HTTPS `koyeb.app` address for the free pitch. A custom address
such as `demo.example.com` additionally requires ownership of that domain and a
DNS record. Treat free local disk as temporary; download important previews.
