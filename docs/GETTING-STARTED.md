# Getting Started

SHAL Voice PC consists of a Windows backend and an Android Flutter controller.

> The repository is currently being prepared for its first complete public source release. Installation instructions will be finalized when the full source tree and release artifacts are published.

## Planned requirements

### Windows PC

- Windows 11 or a compatible modern Windows version
- Python 3
- Tailscale for private remote connectivity
- optional Piper TTS installation
- access to the configured semantic / vision model provider

### Android

- Flutter-built SHAL Voice PC application
- microphone permission
- Tailscale when controlling the PC remotely

## Planned setup flow

1. Install the Windows backend dependencies.
2. Create a private configuration from the provided example.
3. Start the backend on the private Tailscale interface.
4. Install the Android APK.
5. Pair the phone with the PC.
6. Verify health/status.
7. Test low-risk actions before enabling consequential workflows.

## Security

Never publish your pairing key, provider tokens, remote-access passwords, or signing credentials.

See [../SECURITY.md](../SECURITY.md).
