# Getting Started

SHAL Voice PC consists of a Windows backend and an Android Flutter controller.

> The repository is currently being prepared for its first complete public source release. Installation instructions will be finalized when the full source tree and release artifacts are published.

## Prerequisites

### Windows PC
- Windows 10/11 (64-bit)
- Python 3.10+
- Tailscale (recommended for secure peer-to-peer remote access between PC and phone)
- Optional: Piper Neural TTS for local voice synthesis

### Android Device
- Android 9.0+
- Flutter SDK (if building from source) or download pre-built APK from [Releases](https://github.com/inshalstratrum/SHAL-Voice-PC/releases)
- Microphone permission enabled

---

## 1. Windows PC Backend Setup

1. Open PowerShell or Command Prompt and navigate to `PC-Backend`:
   ```powershell
   cd PC-Backend
   ```

2. Install Python dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

3. Create your configuration from the template:
   ```powershell
   copy config.example.json config.json
   ```
   Edit `config.json` to define your desired application launch paths and an optional pairing key.

4. Start the backend agent:
   ```powershell
   python agent_backend.py
   # Or run silently in the background:
   wscript.exe START_VOICE_PC_AGENT.vbs
   ```
   The backend will start listening on port `8765` (or your configured port).

---

## 2. Android Mobile Controller Setup

### Option A: Install Pre-Built APK
1. Download the latest `SHAL-Voice-PC-v0.x.x.apk` from the [Releases](https://github.com/inshalstratrum/SHAL-Voice-PC/releases) tab.
2. Transfer and install on your Android device.

### Option B: Build from Source
1. Ensure the Flutter SDK is installed:
   ```bash
   cd Mobile-App-Flutter
   flutter pub get
   flutter build apk --release
   ```
2. The generated APK will be in `Mobile-App-Flutter/build/app/outputs/flutter-apk/app-release.apk`.

---

## 3. Pairing and Operation

1. Connect both PC and Android device to the same Tailscale tailnet (or local Wi-Fi network).
2. Launch the **SHAL Voice PC** mobile app on your Android phone.
3. Open **Settings** inside the mobile app:
   - **Server:** `http://<YOUR_PC_TAILSCALE_OR_LAN_IP>:8765`
   - **Pairing Key:** Enter the key configured in `config.json` (or leave empty if no key set).
   - Tap **Save & Test Connection**.
4. Tap the microphone icon and speak your natural language command (e.g., *"Open Chrome"*, *"Volume up"*, *"Show desktop"*).
5. For consequential operations (power off, restart), a visual confirmation gate will prompt on your phone before execution.

---

## Security Best Practices

- Never expose port `8765` directly to the public Internet; always use a private VPN mesh such as **Tailscale**.
- Never commit your `config.json`, pairing keys, or device tokens to public source control.
- Review [SECURITY.md](../SECURITY.md) for details on the action allowlist and confirmation model.
