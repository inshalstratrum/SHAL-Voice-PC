# Contributing to SHAL Voice PC

Thank you for your interest in SHAL Voice PC.

## Good contributions

Useful contributions include:

- reproducible bug reports
- Windows UI Automation compatibility fixes
- computer-vision target-detection improvements
- safety and confirmation improvements
- Flutter mobile UX improvements
- Windows compatibility testing
- documentation corrections
- automated tests
- reliability improvements for observe → act → verify workflows

## Before opening a pull request

1. Keep secrets and machine credentials out of commits.
2. Do not commit pairing keys, API tokens, local databases, logs, APKs, or signing keys.
3. Keep model-driven actions inside the typed allowlisted action architecture.
4. Preserve confirmation gates for consequential actions.
5. Keep changes focused and explain how they were tested.

Recommended backend check:

```powershell
python -m py_compile PC-Backend/agent_backend.py PC-Backend/desktop_bridge.py PC-Backend/start_server.pyw PC-Backend/pairing_helper.pyw
```

Recommended Flutter checks:

```powershell
cd Mobile-App-Flutter
flutter pub get
flutter analyze
```

## Pull-request description

Please include:

- what changed
- why it changed
- how it was tested
- any safety impact
- Windows / Android assumptions
- screenshots or logs only after removing private information

## Security issues

Do not publish live credentials, private pairing material, or sensitive machine information in a public issue.
