# Security Policy

SHAL Voice PC can control a real Windows desktop. Treat configuration and runtime data as sensitive.

## Never publish

- pairing keys or API tokens
- SSH or remote-access credentials
- browser/session credentials
- runtime databases or logs containing private desktop content
- Android signing keys

## Safe deployment

Use a private network such as Tailscale, keep consequential actions confirmation-gated, and keep model-driven behavior behind typed allowlisted actions.

## Reporting

When reporting a security issue, remove credentials and personal data from logs and screenshots.