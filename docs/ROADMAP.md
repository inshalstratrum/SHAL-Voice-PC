# Public Roadmap

SHAL Voice PC is under active development.

## Foundation

- [x] Android Flutter controller
- [x] Windows FastAPI backend
- [x] Tailscale-oriented private transport
- [x] Piper text-to-speech
- [x] Windows UI Automation control layer
- [x] screen capture
- [x] visual target localization
- [x] vision-guided click
- [x] keyboard/mouse fallback
- [x] native Windows actions
- [x] confirmation framework

## Universal-control work

- [ ] persistent observe → act → verify state machine
- [ ] richer visual reasoning and target tracking
- [ ] browser / DOM-aware adapter
- [ ] typed file-system operations with verification
- [ ] clipboard state and safe restore
- [ ] additional native Windows settings adapters
- [ ] global task interruption and cancellation hardening
- [ ] long-running task monitoring
- [ ] application-specific adapters
- [ ] retry and recovery policy across more real applications

## Mobile UX

- [ ] clearer task-state display
- [ ] richer live-screen feedback
- [ ] current-action overlay
- [ ] improved interruption UX
- [ ] release-channel update flow

## Reliability

- [ ] expanded Windows-version compatibility testing
- [ ] reusable automated acceptance tests
- [ ] crash and recovery testing
- [ ] network interruption tests
- [ ] packaged installer / updater

## Long-term goal

A user should be able to describe the desired result naturally while SHAL Voice PC determines how to operate the Windows desktop safely and verifies each meaningful step.
