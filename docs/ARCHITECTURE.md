# Architecture

SHAL Voice PC is designed as a multimodal Windows computer-use agent.

## Control stack

```mermaid
flowchart TD
    Goal["Natural-language goal"] --> Observe["Observe current desktop"]

    Observe --> VisionState["Visual screen state"]
    Observe --> UIAState["Windows UI Automation tree"]
    Observe --> NativeState["Native Windows state"]
    Observe --> TaskState["Task and conversation context"]

    VisionState --> Planner["Planner / policy"]
    UIAState --> Planner
    NativeState --> Planner
    TaskState --> Planner

    Planner --> Native["Native Windows action"]
    Planner --> UIA["UI Automation"]
    Planner --> Vision["Vision-guided screen action"]
    Planner --> KM["Keyboard / mouse"]

    Native --> Verify["Verify result"]
    UIA --> Verify
    Vision --> Verify
    KM --> Verify

    Verify --> Done{"Goal complete?"}
    Done -- No --> Observe
    Done -- Yes --> Result["Return / speak result"]
```

## Layer 1 — Windows UI Automation

Used for structured Windows controls when available:

- buttons
- text/edit fields
- menu items
- tabs
- lists
- combo boxes
- checkboxes and toggles
- focus
- expand/collapse
- control-state reading
- element bounds

## Layer 2 — Vision-based screen control

Used when controls are visually present but missing or incomplete in accessibility metadata.

The vision layer can:

- capture the active window or desktop
- locate a target from screenshot pixels
- estimate target bounds and confidence
- correlate the target with accessibility bounds when possible
- click, double-click, or right-click a detected target
- wait for a visible state
- verify screen change after an action

## Layer 3 — Keyboard and mouse automation

General desktop fallback:

- keyboard shortcuts
- text entry
- pointer movement
- click / double-click / right-click
- scrolling
- drag operations

## Layer 4 — Native Windows / system control

Preferred when a typed OS operation is more reliable than GUI manipulation:

- application launch and focus
- windows
- files and folders
- processes
- services
- media
- power
- networking and other system functions

## Safety boundary

The planner should select typed, allowlisted actions. Consequential actions can require explicit confirmation.

The project does not aim to bypass Windows authentication, UAC Secure Desktop, BIOS/UEFI security, CAPTCHA systems, or third-party anti-automation protections.
