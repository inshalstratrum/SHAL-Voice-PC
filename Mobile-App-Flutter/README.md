# SHAL Voice PC v0.4.0

Private Android voice + visual controller for the SHAL Windows PC.

## Final APK

- `dist\SHAL-Voice-PC-v0.4.0.apk`
- `dist\SHAL-Voice-PC-latest.apk`
- SHA-256 is recorded in `dist\SHA256.txt`.

Current SHA-256:
`7E6C6C3F3A02EDD2524F9BA15D8445FD241CB6EC5BB8A39264E4726D520E5B87`

The APK is an internal/sideload build signed with the same Android debug certificate used for v0.1.0. It can normally be installed over v0.1.0 without clearing the pairing settings.

## Current capabilities

### Correct Antigravity and Codex targets
- Antigravity now opens:
  `C:\Users\ROC STORE\AppData\Local\Programs\antigravity\Antigravity.exe`
  This is the Electron Antigravity application, not Antigravity IDE.
- Codex now targets the ChatGPT desktop application in its Codex mode, not the Codex CLI.

### Read / summarize named app responses
Commands such as:
- "summarize the Antigravity last response"
- "read the latest Codex response"

now read the named application's current UI content and summarize the latest response directly. Merely reading/summarizing does NOT require confirmation.

Sending a prompt or handing content to another application remains confirmation-gated.

### Natural voice + Stop
- Natural Voice uses the local PC Piper voice:
  `en_US-lessac-high`
- Markdown, code fences, backticks, URLs, brackets, slash-heavy paths, and similar visual syntax are cleaned before speech.
- A **Stop voice** button appears during playback.
- Tapping the microphone while audio is speaking also stops playback before listening.
- Android TTS remains a fallback if the PC Piper endpoint is unavailable.

### Live PC screen
The app can display a private screenshot stream over Tailscale.
- Tap = left click
- Double tap = open / double click
- Hold = right click
- Scroll Up / Down buttons are provided
- The screen is refreshed approximately once per second.

This is intentionally a low-bandwidth independent control path. RustDesk remains better for high-frame-rate video.

### File Explorer voice/touch flow
Supported actions include:
- "minimize all"
- "minimize all and open my PC"
- "open This PC"
- "open E drive"
- "open selected"
- "go back"
- "go up one folder"
- "copy selected"
- "paste here" (confirmation required)
- "select next"
- "select previous"
- "scroll up"
- "scroll down"

A useful workflow is:
1. Enable Live PC screen.
2. Tap a file or folder to select it.
3. Say "copy selected".
4. Navigate visually or by voice.
5. Say "paste here" and confirm.

### Existing controls retained
- PC health/status
- RustDesk restart
- Windows lock
- Sleep now
- AC sleep timeout / Never Sleep
- Restart and shutdown with confirmation
- Recent action history
- OmniRoute intent interpretation for commands outside the deterministic local rules

## Pairing

The app remains paired to the backend through:
- private Tailscale server address
- per-install pairing key stored with Flutter Secure Storage

Default/current SHAL endpoint:
`http://100.90.31.86:8765`

If the Tailscale IP changes, open the Windows Switch-Board and use:
**SHAL Voice PC Agent -> Copy Phone Pairing**

## Backend safety

The LLM does not receive arbitrary PowerShell access.
It can only select from explicit actions implemented by the backend.

Confirmation is required for higher-impact actions including:
- sleep
- restart
- shutdown
- sending prompts/handoffs to another AI app
- File Explorer paste

## RustDesk

The SHAL app now has its own visual control channel and does not depend on RustDesk for basic screen observation.

For smooth/high-frame-rate remote desktop, RustDesk remains useful. Android RustDesk 1.4.9 currently has a reported resume/background DNS error matching the error seen on the POCO C65. Keep both RustDesk and Tailscale on No Restrictions + Autostart.

The PC currently has direct IP access listening on TCP 21118. Prefer the SHAL Tailscale IP in RustDesk's direct-IP workflow.

See:
`..\..\remote-access-control-center-python\RUSTDESK_ANDROID_STABILITY.txt`

## Build verification

v0.4.0:
- package: `com.shal.voice.shal_voice_pc`
- versionCode: 4
- versionName: 0.2.2
- minSdk: 24
- targetSdk: 36
- compileSdk: 36
- Internet permission: present
- Microphone permission: present
- APK Signature Scheme v2: verified

