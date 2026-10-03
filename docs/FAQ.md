# FAQ

## What is SHAL Voice PC?

SHAL Voice PC is an experimental Windows voice-control and AI desktop-automation project that uses an Android phone as the controller.

## How is it different from a normal voice assistant?

The project is designed for multi-step computer-use tasks rather than only fixed commands. It combines screen vision, Windows UI Automation, keyboard/mouse automation, and native Windows actions.

## Can it see what is on the Windows screen?

Yes. The vision layer is designed to inspect screenshots, identify visible controls, locate targets, and verify visual state changes.

## Does it only use computer vision?

No. Vision is one of four control layers. Structured Windows UI Automation and native Windows actions are preferred when they are more reliable.

## Can it control applications that do not expose accessibility controls?

The vision layer is intended to cover many cases where accessibility metadata is missing by locating visible targets directly on screen.

## Is SHAL Voice PC a remote desktop application?

It includes remote-control capabilities, but its main goal is AI-driven computer operation from natural-language goals. RustDesk and OpenSSH can remain independent recovery tools.

## Does it use Windows UI Automation?

Yes. The UI Automation layer supports common controls such as buttons, edit fields, menu items, tabs, lists, combo boxes, checkboxes, toggles, focus, and expand/collapse operations.

## Is it safe to expose the backend to the public Internet?

No. The intended design uses a private network such as Tailscale and confirmation gates for consequential actions.

## Is it fully autonomous already?

Not yet. The project is actively developing the persistent observe → act → verify → recover loop and broader cross-application reliability.

## Is an Android APK available?

The public release process is being prepared. APKs are intended to be published through GitHub Releases rather than committed into the repository.

## What technologies are involved?

The project uses Python, FastAPI, Flutter, Windows UI Automation, screenshot/computer-vision processing, PowerShell, Piper text-to-speech, and Tailscale-oriented private networking.

## Who is this project for?

Developers, researchers, accessibility experimenters, home-lab users, and people interested in AI computer-use agents, Windows desktop automation, multimodal assistants, and voice-controlled PCs.
