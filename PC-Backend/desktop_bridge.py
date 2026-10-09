import io
import os
import re
import json
import time
import csv
import hashlib
import tempfile
import shutil
import difflib
import ctypes
import ctypes.wintypes
import subprocess
from pathlib import Path
from PIL import ImageGrab, Image

CREATE_NO_WINDOW = 0x08000000
BASE = Path(__file__).resolve().parent
RUNTIME = BASE / "runtime"
RUNTIME.mkdir(exist_ok=True)

PIPER_EXE = Path(r"E:\00 - Master Tools & Switch Board\Tools\piper-tts\piper\piper.exe")
PIPER_MODEL = Path(r"E:\00 - Master Tools & Switch Board\Tools\piper-tts\voices\en_US-lessac-high.onnx")

def run_ps(script, timeout=30):
    cp=subprocess.run(
        ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",script],
        capture_output=True,text=True,timeout=timeout,
        creationflags=CREATE_NO_WINDOW
    )
    return cp.returncode,cp.stdout.strip(),cp.stderr.strip()

def clean_for_speech(text):
    s=str(text or "")
    s=re.sub(r"\x60\x60\x60[\s\S]*?\x60\x60\x60"," code block omitted ",s)
    s=re.sub(r"\x60([^\x60]*)\x60",r"\1",s)
    s=re.sub(r"https?://\S+"," link ",s)
    s=re.sub(r"\[([^\]]+)\]\([^\)]+\)",r"\1",s)
    s=re.sub(r"[*_#>|~]+"," ",s)
    s=s.replace("\\"," ").replace("/"," ")
    s=re.sub(r"[\[\]{}<>]"," ",s)
    s=re.sub(r"\s*[:;]\s*",". ",s)
    s=re.sub(r"\s+"," ",s).strip()
    return s[:6000]

def synthesize_piper(text):
    spoken=clean_for_speech(text)
    if not spoken:
        raise RuntimeError("No speech text.")
    if not PIPER_EXE.exists() or not PIPER_MODEL.exists():
        raise RuntimeError("Piper voice is not installed.")
    out=RUNTIME / f"tts-{int(time.time()*1000)}-{os.getpid()}.wav"
    cp=subprocess.run(
        [str(PIPER_EXE),"--model",str(PIPER_MODEL),"--output_file",str(out)],
        input=spoken,text=True,capture_output=True,timeout=60,
        creationflags=CREATE_NO_WINDOW
    )
    if cp.returncode or not out.exists():
        raise RuntimeError((cp.stderr or cp.stdout or "Piper failed")[-1000:])
    data=out.read_bytes()
    try:
        out.unlink()
    except Exception:
        pass
    return data

def _process_name(meta):
    return str(meta.get("process") or meta.get("title") or "").replace(".exe","")

def process_running(process_name):
    name=str(process_name or "").replace(".exe","").strip()
    if not name:
        return False
    safe=name.replace("'","''")
    rc,out,err=run_ps(f"if(Get-Process -Name '{safe}' -ErrorAction SilentlyContinue){{'YES'}}else{{'NO'}}",10)
    return rc==0 and out.strip().endswith("YES")

def launch_app(target, meta):
    kind=meta.get("kind")
    value=meta.get("value")
    existing=_process_name(meta)
    if existing and focus_process(existing):
        return f"Switched to {target}."
    if kind=="exe":
        try:
            subprocess.Popen([str(value)],creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except FileNotFoundError:
            return open_start_app(str(meta.get("title") or target))
    elif kind=="shell":
        shell_path="shell:AppsFolder\\"+str(value)
        safe_shell=shell_path.replace("'","''")
        rc,out,err=run_ps(f"Start-Process '{safe_shell}'")
        if rc:
            raise RuntimeError(err or out or f"Could not open {target}.")
    else:
        raise RuntimeError(f"Unsupported app type for {target}.")
    return f"Opened {target}."

def focus_process(process_name):
    safe=process_name.replace("'","''")
    script=f"""$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALWin {{
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd,int nCmdShow);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$p=Get-Process -Name '{safe}' -ErrorAction SilentlyContinue | Where-Object {{$_.MainWindowHandle -ne 0}} | Select-Object -First 1
if(-not $p){{'NO';exit}}
[SHALWin]::ShowWindowAsync($p.MainWindowHandle,9)|Out-Null
Start-Sleep -Milliseconds 150
if([SHALWin]::SetForegroundWindow($p.MainWindowHandle)){{'OK'}}else{{'NO'}}"""
    rc,out,err=run_ps(script,15)
    return rc==0 and out.strip().endswith("OK")

def paste_submit_process(process_name,text):
    safe_proc=process_name.replace("'","''")
    safe_text=str(text).replace("'@","' + '@")
    script=f"""Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms
$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALFocus {{
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd,int nCmdShow);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$p=Get-Process -Name '{safe_proc}' -ErrorAction SilentlyContinue | Where-Object {{$_.MainWindowHandle -ne 0}} | Select-Object -First 1
if(-not $p){{throw 'Target window not found'}}
[SHALFocus]::ShowWindowAsync($p.MainWindowHandle,9)|Out-Null
[SHALFocus]::SetForegroundWindow($p.MainWindowHandle)|Out-Null
$root=[System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$input=$null
foreach($e in $all){{
  try{{$t=$e.Current.ControlType.ProgrammaticName;$n=$e.Current.Name}}catch{{continue}}
  if(($t -eq 'ControlType.Edit' -and $n -eq 'Do anything') -or
     ($t -eq 'ControlType.ComboBox' -and $n -eq 'Message input')){{$input=$e}}
}}
if(-not $input){{
  foreach($e in $all){{
    try{{$t=$e.Current.ControlType.ProgrammaticName}}catch{{continue}}
    if($t -eq 'ControlType.Edit' -or $t -eq 'ControlType.ComboBox'){{$input=$e}}
  }}
}}
if(-not $input){{throw 'Message input control not found'}}
$input.SetFocus()
Set-Clipboard -Value @'
{safe_text}
'@
Start-Sleep -Milliseconds 150
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 180
[System.Windows.Forms.SendKeys]::SendWait('{{ENTER}}')
'OK'"""
    rc,out,err=run_ps(script,25)
    return rc==0 and "OK" in out,(err or out)

def prompt_app(target,meta,prompt):
    proc=_process_name(meta)
    running=False
    if proc:
        safe=proc.replace("'","''")
        rc,out,err=run_ps(f"(Get-Process -Name '{safe}' -ErrorAction SilentlyContinue | Where-Object {{$_.MainWindowHandle -ne 0}} | Measure-Object).Count")
        running=(out.strip().isdigit() and int(out.strip())>0)
    if not running:
        launch_app(target,meta)
        time.sleep(2.5)
    ok,msg=paste_submit_process(proc,prompt)
    if not ok:
        return f"Opened {target}, but automatic prompt entry failed: {msg}"
    return f"Submitted the prompt to {target}."

def _uia_text_for_process(process_name,mode="all",limit=18000):
    safe=process_name.replace("'","''")
    if mode=="antigravity":
        body=r"""
$groups=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$matches=@()
foreach($e in $groups){
 try{$t=$e.Current.ControlType.ProgrammaticName;$n=$e.Current.Name}catch{continue}
 if($t -eq 'ControlType.Group' -and $n -eq 'Agent response'){$matches += $e}
}
if($matches.Count -eq 0){exit}
$g=$matches[$matches.Count-1]
$all=$g.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
foreach($e in $all){
 try{$t=$e.Current.ControlType.ProgrammaticName;$n=$e.Current.Name}catch{continue}
 if(($t -eq 'ControlType.Text' -or $t -eq 'ControlType.ListItem') -and $n -and $n -notmatch '^Worked for '){$n}
}
"""
    else:
        body=r"""
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
foreach($e in $all){
 try{$t=$e.Current.ControlType.ProgrammaticName;$n=$e.Current.Name}catch{continue}
 if($t -eq 'ControlType.Text' -and $n){$n}
}
"""
    script=f"""Add-Type -AssemblyName UIAutomationClient
$p=Get-Process -Name '{safe}' -ErrorAction SilentlyContinue | Where-Object {{$_.MainWindowHandle -ne 0}} | Select-Object -First 1
if(-not $p){{exit}}
$root=[System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
{body}
"""
    rc,out,err=run_ps(script,25)
    text=out or err
    if len(text)>limit:
        text=text[-limit:]
    return text.strip()

def latest_response(target,meta):
    proc=_process_name(meta)
    if target=="antigravity":
        text=_uia_text_for_process(proc,"antigravity")
        return text or "I could not read the latest Antigravity response."
    text=_uia_text_for_process(proc,"all")
    if not text:
        return f"I could not read the latest {target} response."
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    markers=[i for i,x in enumerate(lines) if x.lower()=="chatgpt said:"]
    if markers:
        start=markers[-1]+1
        end=len(lines)
        for j in range(start,len(lines)):
            if lines[j].lower()=="you said:":
                end=j
                break
        part="\n".join(lines[start:end]).strip()
        if part:
            return part[-14000:]
    return "\n".join(lines[-160:])[-14000:]

def active_window_text(limit=16000):
    script=r"""$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALFG { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
Add-Type $code -ErrorAction SilentlyContinue
Add-Type -AssemblyName UIAutomationClient
$h=[SHALFG]::GetForegroundWindow()
$root=[System.Windows.Automation.AutomationElement]::FromHandle($h)
"WINDOW: $($root.Current.Name)"
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
foreach($e in $all){
 try{$t=$e.Current.ControlType.ProgrammaticName;$n=$e.Current.Name}catch{continue}
 if(($t -eq 'ControlType.Text' -or $t -eq 'ControlType.ListItem') -and $n){$n}
}"""
    rc,out,err=run_ps(script,20)
    text=out or err
    return text[-limit:].strip()

def minimize_all():
    rc,out,err=run_ps("$s=New-Object -ComObject Shell.Application; $s.MinimizeAll()",10)
    if rc:
        raise RuntimeError(err or out or "Could not minimize windows.")
    return "Minimized all windows."

def open_this_pc():
    subprocess.Popen(["explorer.exe","shell:MyComputerFolder"],creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return "Opened This PC."

def open_drive(letter):
    letter=str(letter).strip().upper().replace(":","")
    if not re.fullmatch(r"[A-Z]",letter):
        raise ValueError("Drive letter is invalid.")
    path=Path(f"{letter}:\\")
    if not path.exists():
        raise ValueError(f"Drive {letter} is not available.")
    subprocess.Popen(["explorer.exe",str(path)],creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return f"Opened drive {letter}."

def open_path(path):
    p=Path(str(path)).expanduser()
    if not p.exists():
        raise ValueError("That path does not exist.")
    subprocess.Popen(["explorer.exe",str(p)],creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return f"Opened {p.name or str(p)}."

def explorer_keys(action):
    keys={
        "open_selected":"{ENTER}",
        "back":"%{LEFT}",
        "forward":"%{RIGHT}",
        "up":"%{UP}",
        "copy":"^c",
        "paste":"^v",
        "next":"{DOWN}",
        "previous":"{UP}",
    }
    if action not in keys:
        raise ValueError("Unsupported Explorer action.")
    key=keys[action]
    script=f"""Add-Type -AssemblyName System.Windows.Forms
$code=@'
using System;
using System.Runtime.InteropServices;
public class FGP {{
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd,out uint id);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$h=[FGP]::GetForegroundWindow(); [uint32]$pid=0; [FGP]::GetWindowThreadProcessId($h,[ref]$pid)|Out-Null
$p=Get-Process -Id $pid -ErrorAction SilentlyContinue
if(-not $p -or $p.ProcessName -ne 'explorer'){{throw 'File Explorer is not the active window'}}
[System.Windows.Forms.SendKeys]::SendWait('{key}')
'OK'"""
    rc,out,err=run_ps(script,10)
    if rc:
        raise RuntimeError(err or out or "Explorer action failed.")
    labels={
        "open_selected":"Opened the selected item.",
        "back":"Went back.",
        "forward":"Went forward.",
        "up":"Went up one folder.",
        "copy":"Copied the selected item.",
        "paste":"Pasted into the current folder.",
        "next":"Selected the next item.",
        "previous":"Selected the previous item."
    }
    return labels[action]

def screen_jpeg(max_width=1280,quality=55):
    img=ImageGrab.grab(all_screens=True)
    ow,oh=img.size
    if ow>max_width:
        nh=max(1,int(oh*max_width/ow))
        img=img.resize((max_width,nh))
    buf=io.BytesIO()
    img.convert("RGB").save(buf,format="JPEG",quality=int(quality),optimize=True)
    return buf.getvalue(),ow,oh,img.size[0],img.size[1]

def _virtual_screen():
    u=ctypes.windll.user32
    x=u.GetSystemMetrics(76); y=u.GetSystemMetrics(77)
    w=u.GetSystemMetrics(78); h=u.GetSystemMetrics(79)
    if w<=0 or h<=0:
        w=u.GetSystemMetrics(0); h=u.GetSystemMetrics(1); x=0; y=0
    return x,y,w,h

def pointer_action(x_norm,y_norm,kind="click"):
    x0,y0,w,h=_virtual_screen()
    x=x0+int(max(0,min(float(x_norm),1))*max(w-1,1))
    y=y0+int(max(0,min(float(y_norm),1))*max(h-1,1))
    u=ctypes.windll.user32
    u.SetCursorPos(x,y)
    left_down=0x0002; left_up=0x0004
    right_down=0x0008; right_up=0x0010
    if kind=="right":
        u.mouse_event(right_down,0,0,0,0); u.mouse_event(right_up,0,0,0,0)
    elif kind=="double":
        for _ in range(2):
            u.mouse_event(left_down,0,0,0,0); u.mouse_event(left_up,0,0,0,0); time.sleep(0.08)
    else:
        u.mouse_event(left_down,0,0,0,0); u.mouse_event(left_up,0,0,0,0)
    return {"x":x,"y":y,"kind":kind}

def scroll_screen(delta):
    ctypes.windll.user32.mouse_event(0x0800,0,0,int(delta)*120,0)
    return {"delta":int(delta)}

def pointer_drag(x1_norm,y1_norm,x2_norm,y2_norm,duration=0.55):
    """Drag between two normalized desktop coordinates."""
    x0,y0,w,h=_virtual_screen()
    sx=x0+int(max(0,min(float(x1_norm),1))*max(w-1,1))
    sy=y0+int(max(0,min(float(y1_norm),1))*max(h-1,1))
    ex=x0+int(max(0,min(float(x2_norm),1))*max(w-1,1))
    ey=y0+int(max(0,min(float(y2_norm),1))*max(h-1,1))
    u=ctypes.windll.user32
    u.SetCursorPos(sx,sy)
    time.sleep(0.06)
    u.mouse_event(0x0002,0,0,0,0)
    steps=max(8,min(40,int(max(float(duration),0.15)*30)))
    pause=max(float(duration),0.15)/steps
    try:
        for i in range(1,steps+1):
            x=int(sx+(ex-sx)*i/steps); y=int(sy+(ey-sy)*i/steps)
            u.SetCursorPos(x,y); time.sleep(pause)
    finally:
        u.mouse_event(0x0004,0,0,0,0)
    return {"start":{"x":sx,"y":sy},"end":{"x":ex,"y":ey},"duration":round(max(float(duration),0.15),3)}

def active_window_rect():
    """Return foreground window bounds in virtual-screen pixel coordinates."""
    hwnd=ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect=ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd,ctypes.byref(rect)):
        return None
    x0,y0,w,h=_virtual_screen()
    left=max(x0,int(rect.left)); top=max(y0,int(rect.top))
    right=min(x0+w,int(rect.right)); bottom=min(y0+h,int(rect.bottom))
    if right<=left or bottom<=top:
        return None
    return {"left":left,"top":top,"right":right,"bottom":bottom,
            "width":right-left,"height":bottom-top,
            "virtual_left":x0,"virtual_top":y0,"virtual_width":w,"virtual_height":h}

def screen_capture_jpeg(scope="desktop",max_width=1400,quality=58):
    """Capture full virtual desktop or the active-window crop with coordinate metadata."""
    img=ImageGrab.grab(all_screens=True).convert("RGB")
    vx,vy,vw,vh=_virtual_screen()
    meta={"scope":"desktop","origin_x":vx,"origin_y":vy,"native_width":img.width,"native_height":img.height,
          "virtual_width":vw,"virtual_height":vh}
    if str(scope).lower() in {"active","active_window","window"}:
        r=active_window_rect()
        if r:
            l=max(0,r["left"]-vx); t=max(0,r["top"]-vy)
            rr=min(img.width,r["right"]-vx); bb=min(img.height,r["bottom"]-vy)
            if rr>l and bb>t:
                img=img.crop((l,t,rr,bb))
                meta.update({"scope":"active_window","origin_x":r["left"],"origin_y":r["top"],
                             "native_width":img.width,"native_height":img.height})
    if max_width and img.width>int(max_width):
        nh=max(1,int(img.height*int(max_width)/img.width))
        img=img.resize((int(max_width),nh))
    meta["capture_width"]=img.width; meta["capture_height"]=img.height
    buf=io.BytesIO(); img.save(buf,format="JPEG",quality=int(quality),optimize=True)
    return buf.getvalue(),meta


# ---- General Windows voice-control helpers ----

def _foreground_window_info():
    u=ctypes.windll.user32
    hwnd=u.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("No active window was found.")
    length=u.GetWindowTextLengthW(hwnd)
    buf=ctypes.create_unicode_buffer(max(length+1,2))
    u.GetWindowTextW(hwnd,buf,len(buf))
    pid=ctypes.c_ulong(0)
    u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    title=buf.value.strip() or "Untitled window"
    return hwnd,int(pid.value),title

def _key_combo(keys):
    u=ctypes.windll.user32
    vk={
        "alt":0x12,"shift":0x10,"ctrl":0x11,"win":0x5B,
        "tab":0x09,"enter":0x0D,"escape":0x1B,"space":0x20,"delete":0x2E,
        "pageup":0x21,"pagedown":0x22,"end":0x23,"home":0x24,
        "left":0x25,"up":0x26,"right":0x27,"down":0x28,
        "f2":0x71,"f4":0x73,"f5":0x74,"a":0x41,"c":0x43,"e":0x45,"f":0x46,
        "i":0x49,"l":0x4C,"r":0x52,"t":0x54,"w":0x57,"x":0x58,
        "v":0x56,"z":0x5A,"y":0x59,"s":0x53,"n":0x4E,
    }
    vals=[vk[k] for k in keys]
    for v in vals:
        u.keybd_event(v,0,0,0)
        time.sleep(0.025)
    for v in reversed(vals):
        u.keybd_event(v,0,0x0002,0)
        time.sleep(0.025)

def window_action(action):
    action=str(action or "").lower().strip()
    if action=="restore_all":
        rc,out,err=run_ps("$s=New-Object -ComObject Shell.Application; $s.UndoMinimizeALL()",10)
        if rc:
            raise RuntimeError(err or out or "Could not restore minimized windows.")
        return "Restored minimized windows."

    if action=="next":
        _key_combo(["alt","tab"])
        return "Switched to the next window."
    if action=="previous":
        _key_combo(["alt","shift","tab"])
        return "Switched to the previous window."

    hwnd,pid,title=_foreground_window_info()
    u=ctypes.windll.user32
    if action=="minimize":
        u.ShowWindowAsync(hwnd,6)
        return f"Minimized {title}."
    if action=="maximize":
        u.ShowWindowAsync(hwnd,3)
        return f"Maximized {title}."
    if action=="restore":
        u.ShowWindowAsync(hwnd,9)
        u.SetForegroundWindow(hwnd)
        return f"Restored {title}."
    if action=="close":
        if not u.PostMessageW(hwnd,0x0010,0,0):
            raise RuntimeError("Windows did not accept the close request.")
        return f"Close requested for {title}."
    if action=="left":
        u.SetForegroundWindow(hwnd)
        _key_combo(["win","left"])
        return f"Snapped {title} to the left."
    if action=="right":
        u.SetForegroundWindow(hwnd)
        _key_combo(["win","right"])
        return f"Snapped {title} to the right."
    raise ValueError("Unsupported window action.")

def focus_named_process(process_name,label=None):
    name=str(process_name or "").replace(".exe","").strip()
    if not name:
        raise ValueError("No process name was supplied.")
    if not focus_process(name):
        raise RuntimeError(f"{label or name} is not open.")
    return f"Switched to {label or name}."

def active_process_name():
    hwnd,pid,title=_foreground_window_info()
    rc,out,err=run_ps(f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName",10)
    return (out.strip() if rc==0 and out.strip() else ""),title

def explorer_window_action(action):
    proc,title=active_process_name()
    if proc.lower()!="explorer":
        raise RuntimeError("File Explorer is not the active window.")
    if action=="close":
        return window_action("close")
    return explorer_keys(action)

def keyboard_action(action):
    action=str(action or "").lower().strip()
    combos={
        "enter":["enter"],
        "escape":["escape"],
        "tab":["tab"],
        "shift_tab":["shift","tab"],
        "select_all":["ctrl","a"],
        "copy":["ctrl","c"],
        "cut":["ctrl","x"],
        "paste":["ctrl","v"],
        "undo":["ctrl","z"],
        "redo":["ctrl","y"],
        "save":["ctrl","s"],
        "delete":["delete"],
        "rename":["f2"],
        "refresh":["f5"],
        "new_folder":["ctrl","shift","n"],
        "back":["alt","left"],
        "forward":["alt","right"],
        "find":["ctrl","f"],
        "new_tab":["ctrl","t"],
        "close_tab":["ctrl","w"],
        "reopen_tab":["ctrl","shift","t"],
        "open_explorer":["win","e"],
        "open_settings":["win","i"],
        "lock":["win","l"],
        "show_desktop":["win","d"],
        "task_view":["win","tab"],
        "run_dialog":["win","r"],
        "start_menu":["win"],
        "search":["win","s"],
        "quick_settings":["win","a"],
        "notifications":["win","n"],
        "clipboard_history":["win","v"],
        "screenshot":["win","shift","s"],
        "new_desktop":["win","ctrl","d"],
        "close_desktop":["win","ctrl","f4"],
        "desktop_left":["win","ctrl","left"],
        "desktop_right":["win","ctrl","right"],
        "task_manager":["ctrl","shift","escape"],
        "home":["home"],
        "end":["end"],
        "page_up":["pageup"],
        "page_down":["pagedown"],
        "space":["space"],
    }
    if action not in combos:
        raise ValueError("Unsupported keyboard action.")
    _key_combo(combos[action])
    labels={
        "enter":"Pressed Enter.","escape":"Pressed Escape.","tab":"Pressed Tab.",
        "shift_tab":"Moved to the previous field.","select_all":"Selected all.",
        "copy":"Copied.","cut":"Cut the selection.","paste":"Pasted.",
        "undo":"Undid the last action.","redo":"Redid the last action.",
        "save":"Requested Save.","delete":"Pressed Delete.","rename":"Started rename.",
        "refresh":"Refreshed the window.","new_folder":"Started a new folder.",
        "back":"Went back.","forward":"Went forward.","find":"Opened Find.",
        "new_tab":"Opened a new tab.","close_tab":"Closed the current tab.","reopen_tab":"Reopened the last tab.",
        "open_explorer":"Opened File Explorer.","open_settings":"Opened Windows Settings.",
        "lock":"Locked Windows.","show_desktop":"Toggled the desktop.","task_view":"Opened Task View.",
        "run_dialog":"Opened the Run dialog.","start_menu":"Opened the Start menu.","search":"Opened Windows Search.",
        "quick_settings":"Opened Quick Settings.","notifications":"Opened notifications.","clipboard_history":"Opened clipboard history.",
        "screenshot":"Opened the screenshot tool.","new_desktop":"Created a new virtual desktop.","close_desktop":"Closed the current virtual desktop.",
        "desktop_left":"Switched to the desktop on the left.","desktop_right":"Switched to the desktop on the right.",
        "task_manager":"Opened Task Manager.","home":"Moved to Home.","end":"Moved to End.",
        "page_up":"Moved one page up.","page_down":"Moved one page down.","space":"Pressed Space."
    }
    return labels[action]

def type_text(text,submit=False):
    value=str(text or "")
    if not value:
        raise ValueError("There is no text to type.")
    safe=value.replace("'@","' + '@")
    script=f"""Add-Type -AssemblyName System.Windows.Forms
Set-Clipboard -Value @'
{safe}
'@
Start-Sleep -Milliseconds 120
[System.Windows.Forms.SendKeys]::SendWait('^v')
"""
    if submit:
        script += "\nStart-Sleep -Milliseconds 120\n[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"
    rc,out,err=run_ps(script,15)
    if rc:
        raise RuntimeError(err or out or "Typing failed.")
    return "Typed the text" + (" and pressed Enter." if submit else ".")

def list_visible_windows(limit=12):
    script=r"""Get-Process | Where-Object {$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle} |
Sort-Object ProcessName |
Select-Object -First 30 ProcessName,MainWindowTitle |
ForEach-Object {"$($_.ProcessName)|$($_.MainWindowTitle)"}"""
    rc,out,err=run_ps(script,15)
    if rc:
        raise RuntimeError(err or out or "Could not list open windows.")
    rows=[]
    for line in out.splitlines():
        if "|" not in line:
            continue
        proc,title=line.split("|",1)
        rows.append({"process":proc.strip(),"title":title.strip()})
        if len(rows)>=int(limit):
            break
    return rows


def explorer_rename(new_name):
    proc,title=active_process_name()
    if proc.lower()!="explorer":
        raise RuntimeError("File Explorer is not the active window.")
    name=str(new_name or "").strip()
    if not name:
        raise ValueError("A new name is required.")
    keyboard_action("rename")
    time.sleep(0.35)
    return type_text(name,submit=True)

def explorer_new_folder(name):
    proc,title=active_process_name()
    if proc.lower()!="explorer":
        raise RuntimeError("File Explorer is not the active window.")
    folder=str(name or "").strip()
    if not folder:
        raise ValueError("A folder name is required.")
    keyboard_action("new_folder")
    time.sleep(0.35)
    return type_text(folder,submit=True)


def app_window(meta,label,action):
    proc=_process_name(meta)
    if not proc:
        raise RuntimeError(f"No process mapping exists for {label}.")
    if not process_running(proc):
        if action=="close":
            raise RuntimeError(f"{label} is not open.")
        launch_app(label,meta)
        time.sleep(2.0)
    if action=="focus":
        return focus_named_process(proc,label)
    return named_window_action(proc,action)


def ui_snapshot(limit=80):
    """Return a compact accessibility map of the active window for the voice planner."""
    script=r"""Add-Type -AssemblyName UIAutomationClient
$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALUIFG { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
Add-Type $code -ErrorAction SilentlyContinue
$h=[SHALUIFG]::GetForegroundWindow()
if($h -eq [IntPtr]::Zero){exit}
$root=[System.Windows.Automation.AutomationElement]::FromHandle($h)
"WINDOW|$($root.Current.Name)"
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$i=0
foreach($e in $all){
  if($i -ge 160){break}
  try{
    $n=$e.Current.Name
    $a=$e.Current.AutomationId
    $t=$e.Current.ControlType.ProgrammaticName -replace '^ControlType\.',''
    $off=$e.Current.IsOffscreen
    $enabled=$e.Current.IsEnabled
    $r=$e.Current.BoundingRectangle
    $rx=[math]::Round($r.X,1); $ry=[math]::Round($r.Y,1)
    $rw=[math]::Round($r.Width,1); $rh=[math]::Round($r.Height,1)
    $toggle=''
    try{
      $tp=$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
      if($tp){$toggle=$tp.Current.ToggleState.ToString()}
    }catch{}
  }catch{continue}
  if((-not $n) -and (-not $a)){continue}
  if($n.Length -gt 180){$n=$n.Substring(0,180)}
  if($a.Length -gt 120){$a=$a.Substring(0,120)}
  "$t|$n|$a|enabled=$enabled|offscreen=$off|toggle=$toggle|rect=$rx,$ry,$rw,$rh"
  $i++
}"""
    rc,out,err=run_ps(script,20)
    if rc:
        return {"window":"","elements":[],"error":err or out}
    window=""
    elements=[]
    for line in out.splitlines():
        if line.startswith("WINDOW|"):
            window=line.split("|",1)[1].strip()
            continue
        parts=line.split("|")
        if len(parts)<3:
            continue
        rect=[0.0,0.0,0.0,0.0]
        toggle=""
        for extra in parts[3:]:
            if extra.startswith("toggle="):
                toggle=extra.split("=",1)[1].strip()
            elif extra.startswith("rect="):
                try:
                    rect=[float(x) for x in extra.split("=",1)[1].split(",")[:4]]
                except Exception:
                    rect=[0.0,0.0,0.0,0.0]
        elements.append({
            "type":parts[0].strip(),
            "name":parts[1].strip(),
            "id":parts[2].strip(),
            "enabled":"enabled=True" in line,
            "offscreen":"offscreen=True" in line,
            "toggle":toggle,
            "rect":rect,
        })
        if len(elements)>=int(limit):
            break
    proc=""
    try:
        proc,_=active_process_name()
    except Exception:
        pass
    return {"window":window,"process":proc,"elements":elements}

def ui_invoke(label,control_type=""):
    """Invoke/select/toggle/focus a visible accessibility element by spoken label."""
    needle=str(label or "").strip()
    role=str(control_type or "").strip()
    if not needle:
        raise ValueError("A control name is required.")
    # Windows Settings often collapses its navigation. If the requested label is
    # only represented by the shell menu (or not yet visible), expand navigation
    # once and then resolve the requested control again.
    if needle.lower()!="open navigation":
        try:
            snap=ui_snapshot(120)
            q=re.sub(r"[^a-z0-9]+"," ",needle.lower()).strip()
            matched_types=[]
            has_nav=False
            for e in snap.get("elements") or []:
                n=re.sub(r"[^a-z0-9]+"," ",str(e.get("name") or "").lower()).strip()
                if n=="open navigation" and e.get("type")=="Button":
                    has_nav=True
                if n==q:
                    matched_types.append(str(e.get("type") or ""))
            if has_nav and (not matched_types or all(t=="MenuItem" for t in matched_types)):
                try:
                    ui_invoke("Open Navigation")
                    time.sleep(0.55)
                except Exception:
                    pass
        except Exception:
            pass
    safe=needle.replace("'","''")
    saferole=role.replace("'","''")
    script=f"""Add-Type -AssemblyName UIAutomationClient
$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALUIInvokeFG {{
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int X,int Y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint dx,uint dy,uint data,UIntPtr extra);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$h=[SHALUIInvokeFG]::GetForegroundWindow()
if($h -eq [IntPtr]::Zero){{throw 'No active window'}}
$root=[System.Windows.Automation.AutomationElement]::FromHandle($h)
$needle='{safe}'
$role='{saferole}'
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$candidates=@()
foreach($e in $all){{
 try{{
   $n=$e.Current.Name
   $aid=$e.Current.AutomationId
   $type=$e.Current.ControlType.ProgrammaticName -replace '^ControlType\\.',''
   $enabled=$e.Current.IsEnabled
   $off=$e.Current.IsOffscreen
 }}catch{{continue}}
 if(-not $enabled){{continue}}
 if($role -and $type -notlike "*$role*"){{continue}}
 $score=99
 if($n -ieq $needle -or $aid -ieq $needle){{$score=0}}
 elseif($n -and $n.StartsWith($needle,[StringComparison]::OrdinalIgnoreCase)){{$score=1}}
 elseif($n -and $n.IndexOf($needle,[StringComparison]::OrdinalIgnoreCase) -ge 0){{$score=2}}
 elseif($aid -and $aid.IndexOf($needle,[StringComparison]::OrdinalIgnoreCase) -ge 0){{$score=3}}
 if($score -lt 99){{
   if($type -eq 'MenuItem'){{$score+=5}}
   elseif($type -eq 'TreeItem'){{$score+=2}}
   if($off){{$score+=10}}
   $candidates += [pscustomobject]@{{Score=$score;Element=$e;Name=$n;Type=$type}}
 }}
}}
if($candidates.Count -eq 0){{throw "Visible control '$needle' was not found"}}
$done=$false
$c=$null
foreach($candidate in ($candidates | Sort-Object Score)){{
 $e=$candidate.Element
 try{{
  $p=$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
  if($p){{$p.Invoke();$done=$true}}
 }}catch{{}}
 if(-not $done){{try{{
  $p=$e.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
  if($p){{$p.Select();$done=$true}}
 }}catch{{}}}}
 if(-not $done){{try{{
  $p=$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
  if($p){{$p.Toggle();$done=$true}}
 }}catch{{}}}}
 if(-not $done){{try{{
  $p=$e.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  if($p){{$p.Expand();$done=$true}}
 }}catch{{}}}}
 if(-not $done){{try{{
  $r=$e.Current.BoundingRectangle
  if($r.Width -gt 2 -and $r.Height -gt 2 -and -not $candidate.Element.Current.IsOffscreen){{
   $cx=[int]($r.X+($r.Width/2)); $cy=[int]($r.Y+($r.Height/2))
   [SHALUIInvokeFG]::SetCursorPos($cx,$cy)|Out-Null
   Start-Sleep -Milliseconds 80
   [SHALUIInvokeFG]::mouse_event(0x0002,0,0,0,[UIntPtr]::Zero)
   [SHALUIInvokeFG]::mouse_event(0x0004,0,0,0,[UIntPtr]::Zero)
   $done=$true
  }}
 }}catch{{}}}}
 if(-not $done){{try{{
  $e.SetFocus()
  $done=$true
 }}catch{{}}}}
 if($done){{$c=$candidate;break}}
}}
if(-not $done){{throw "Control '$needle' cannot be invoked"}}
"OK|$($c.Type)|$($c.Name)"
"""
    rc,out,err=run_ps(script,20)
    if rc:
        raise RuntimeError(err or out or f"Could not activate {needle}.")
    line=(out.splitlines()[-1] if out else "")
    parts=line.split("|",2)
    actual=parts[2] if len(parts)>2 else needle
    return f"Activated {actual}."

def ui_set_text(label,text,submit=False):
    """Set text in a named Edit control using ValuePattern, with a focused paste fallback."""
    needle=str(label or "").strip()
    value=str(text or "")
    if not value:
        raise ValueError("There is no text to enter.")
    safe_label=needle.replace("'","''")
    safe_value=value.replace("'@","' + '@")
    script=f"""Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms
$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALUIEditFG {{ [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }}
'@
Add-Type $code -ErrorAction SilentlyContinue
$root=[System.Windows.Automation.AutomationElement]::FromHandle([SHALUIEditFG]::GetForegroundWindow())
$needle='{safe_label}'
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$pick=$null
foreach($e in $all){{
 try{{$n=$e.Current.Name;$aid=$e.Current.AutomationId;$t=$e.Current.ControlType.ProgrammaticName;$en=$e.Current.IsEnabled}}catch{{continue}}
 if(-not $en -or $t -ne 'ControlType.Edit'){{continue}}
 if((-not $needle) -or $n -ieq $needle -or $aid -ieq $needle -or ($n -and $n -like "*$needle*") -or ($aid -and $aid -like "*$needle*")){{$pick=$e;break}}
}}
if(-not $pick){{throw "Text field '$needle' was not found"}}
$pick.SetFocus()
$set=$false
try{{
 $vp=$pick.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
 if($vp -and -not $vp.Current.IsReadOnly){{
   $vp.SetValue(@'
{safe_value}
'@)
   $set=$true
 }}
}}catch{{}}
if(-not $set){{
 Set-Clipboard -Value @'
{safe_value}
'@
 [System.Windows.Forms.SendKeys]::SendWait('^a')
 Start-Sleep -Milliseconds 80
 [System.Windows.Forms.SendKeys]::SendWait('^v')
}}
"""
    if submit:
        script += "\nStart-Sleep -Milliseconds 100\n[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"
    script += "\n'OK'"
    rc,out,err=run_ps(script,20)
    if rc:
        raise RuntimeError(err or out or "Could not enter the text.")
    return "Entered the text" + (" and pressed Enter." if submit else ".")

def focus_window(query):
    """Focus a visible window using a title or process-name fragment."""
    q=str(query or "").strip()
    if not q:
        raise ValueError("A window name is required.")
    safe=q.replace("'","''")
    script=f"""$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALFocusAny {{
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd,int nCmdShow);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern void keybd_event(byte bVk,byte bScan,uint dwFlags,UIntPtr dwExtraInfo);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$q='{safe}'
$rx=[regex]::Escape($q)
$candidates=Get-Process | Where-Object {{$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle}} | ForEach-Object {{
 $score=99
 if($_.MainWindowTitle -ieq $q){{$score=0}}
 elseif($_.ProcessName -ieq $q){{$score=1}}
 elseif($_.MainWindowTitle.StartsWith($q,[StringComparison]::OrdinalIgnoreCase)){{$score=2}}
 elseif($_.ProcessName.StartsWith($q,[StringComparison]::OrdinalIgnoreCase)){{$score=3}}
 elseif($_.MainWindowTitle.EndsWith($q,[StringComparison]::OrdinalIgnoreCase)){{$score=4}}
 elseif($_.ProcessName.EndsWith($q,[StringComparison]::OrdinalIgnoreCase)){{$score=5}}
 elseif($_.MainWindowTitle -match "(?i)(^|[^A-Za-z0-9])$rx([^A-Za-z0-9]|$)"){{$score=6}}
 elseif($_.ProcessName -match "(?i)(^|[^A-Za-z0-9])$rx([^A-Za-z0-9]|$)"){{$score=7}}
 if($score -lt 99){{[pscustomobject]@{{Score=$score;P=$_}}}}
}}
$p=($candidates | Sort-Object Score | Select-Object -First 1).P
if(-not $p){{throw "No open window matched '$q'"}}
[SHALFocusAny]::ShowWindowAsync($p.MainWindowHandle,9)|Out-Null
Start-Sleep -Milliseconds 120
[SHALFocusAny]::keybd_event(0x12,0,0,[UIntPtr]::Zero)
[SHALFocusAny]::keybd_event(0x12,0,2,[UIntPtr]::Zero)
[SHALFocusAny]::SetForegroundWindow($p.MainWindowHandle)|Out-Null
Start-Sleep -Milliseconds 120
if([SHALFocusAny]::GetForegroundWindow() -ne $p.MainWindowHandle){{
 $w=New-Object -ComObject WScript.Shell
 $w.AppActivate($p.Id)|Out-Null
 Start-Sleep -Milliseconds 180
 [SHALFocusAny]::SetForegroundWindow($p.MainWindowHandle)|Out-Null
 Start-Sleep -Milliseconds 120
}}
if([SHALFocusAny]::GetForegroundWindow() -ne $p.MainWindowHandle){{throw "Windows refused to focus '$q'"}}
"OK|$($p.ProcessName)|$($p.MainWindowTitle)"
"""
    rc,out,err=run_ps(script,15)
    if rc:
        raise RuntimeError(err or out or f"Could not switch to {q}.")
    title=(out.split("|",2)[-1] if out else q)
    return f"Switched to {title}."

def open_start_app(query):
    """Open an installed Start-menu app by display name, without executing arbitrary shell text."""
    q=str(query or "").strip()
    if not q:
        raise ValueError("An app name is required.")
    safe=q.replace("'","''")
    script=f"""$q='{safe}'
$apps=Get-StartApps
$m=$apps | Where-Object {{$_.Name -ieq $q}} | Select-Object -First 1
if(-not $m){{$m=$apps | Where-Object {{$_.Name -like "$q*"}} | Select-Object -First 1}}
if(-not $m){{$m=$apps | Where-Object {{$_.Name -like "*$q*"}} | Select-Object -First 1}}
if(-not $m){{throw "Installed app '$q' was not found"}}
Start-Process ("shell:AppsFolder\\"+$m.AppID)
Start-Sleep -Milliseconds 1000
$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALStartFocus {{
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd,int nCmdShow);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$p=Get-Process | Where-Object {{$_.MainWindowHandle -ne 0 -and ($_.MainWindowTitle -like "*$($m.Name)*" -or $_.ProcessName -like "*$q*")}} | Select-Object -First 1
if($p){{
 [SHALStartFocus]::ShowWindowAsync($p.MainWindowHandle,9)|Out-Null
 Start-Sleep -Milliseconds 120
 [SHALStartFocus]::SetForegroundWindow($p.MainWindowHandle)|Out-Null
}}
"OK|$($m.Name)"
"""
    rc,out,err=run_ps(script,20)
    if rc:
        raise RuntimeError(err or out or f"Could not open {q}.")
    actual=(out.split("|",1)[1] if "|" in out else q)
    # UWP/Store apps can launch under ApplicationFrameHost and ignore the first
    # foreground request. Re-resolve the real visible window by title and verify
    # that it becomes the foreground window before reporting success.
    last_error=None
    for _ in range(8):
        try:
            focus_window(actual)
            proc_now,title_now=active_process_name()
            if actual.lower() in str(title_now).lower() or q.lower() in str(title_now).lower():
                return f"Opened {actual}."
        except Exception as e:
            last_error=e
        time.sleep(0.25)
    raise RuntimeError(f"Opened {actual}, but could not bring its window to the foreground: {last_error or 'window not found'}")

def open_known_location(name):
    key=str(name or "").strip().lower()
    home=Path(os.environ.get("USERPROFILE",str(Path.home())))
    mapping={
        "desktop":home/"Desktop",
        "downloads":home/"Downloads",
        "documents":home/"Documents",
        "pictures":home/"Pictures",
        "videos":home/"Videos",
        "music":home/"Music",
    }
    if key not in mapping:
        raise ValueError("That known folder is not configured.")
    p=mapping[key]
    subprocess.Popen(["explorer.exe",str(p)],creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return f"Opened {key}."

def media_key(action):
    action=str(action or "").lower()
    codes={
        "volume_up":0xAF,"volume_down":0xAE,"mute":0xAD,
        "play_pause":0xB3,"next_track":0xB0,"previous_track":0xB1,
        "stop_media":0xB2,
    }
    if action not in codes:
        raise ValueError("Unsupported media action.")
    u=ctypes.windll.user32
    vk=codes[action]
    u.keybd_event(vk,0,0,0); u.keybd_event(vk,0,0x0002,0)
    labels={
        "volume_up":"Volume increased.","volume_down":"Volume decreased.","mute":"Toggled mute.",
        "play_pause":"Toggled play or pause.","next_track":"Skipped to the next track.",
        "previous_track":"Went to the previous track.","stop_media":"Stopped media playback."
    }
    return labels[action]


def named_window_action(query,action):
    q=str(query or "").strip()
    act=str(action or "").strip().lower()
    if not q:
        raise ValueError("A window name is required.")
    if act not in {"maximize","minimize","restore","close","left","right"}:
        raise ValueError("Unsupported named window action.")
    safe=q.replace("'","''")
    ps=f"""$code=@'
using System;
using System.Runtime.InteropServices;
public class SHALNamedWin {{
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd,int nCmdShow);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd,uint Msg,IntPtr wParam,IntPtr lParam);
}}
'@
Add-Type $code -ErrorAction SilentlyContinue
$q='{safe}'
$c=Get-Process | Where-Object {{$_.MainWindowHandle -ne 0 -and ($_.MainWindowTitle -like "*$q*" -or $_.ProcessName -like "*$q*")}} | ForEach-Object {{
 $s=50
 if($_.MainWindowTitle -ieq $q){{$s=0}}
 elseif($_.ProcessName -ieq $q){{$s=1}}
 elseif($_.MainWindowTitle -like "$q*"){{$s=2}}
 elseif($_.ProcessName -like "$q*"){{$s=3}}
 elseif($_.MainWindowTitle -like "*$q*"){{$s=4}}
 elseif($_.ProcessName -like "*$q*"){{$s=5}}
 [pscustomobject]@{{Score=$s;P=$_}}
}}
$p=($c | Sort-Object Score | Select-Object -First 1).P
if(-not $p){{throw "No open window matched '$q'"}}
$h=$p.MainWindowHandle
$action='{act}'
if($action -eq 'minimize'){{[SHALNamedWin]::ShowWindowAsync($h,6)|Out-Null}}
elseif($action -eq 'maximize'){{[SHALNamedWin]::ShowWindowAsync($h,3)|Out-Null}}
elseif($action -eq 'restore'){{[SHALNamedWin]::ShowWindowAsync($h,9)|Out-Null}}
elseif($action -eq 'close'){{[SHALNamedWin]::PostMessage($h,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null}}
else{{[SHALNamedWin]::ShowWindowAsync($h,9)|Out-Null;[SHALNamedWin]::SetForegroundWindow($h)|Out-Null}}
"OK|$($p.MainWindowTitle)"
"""
    rc,out,err=run_ps(ps,15)
    if rc:
        # Some modern Windows apps (for example Settings) expose their visible
        # window through a host process that Get-Process does not always return
        # consistently. Fall back to the already-tested focus path, then act on
        # the verified foreground window.
        focus_window(q)
        time.sleep(0.2)
        _,title_now=active_process_name()
        if q.lower() not in str(title_now).lower():
            raise RuntimeError(err or out or f"Could not control {q}.")
        return window_action(act)
    title=(out.split("|",1)[1] if "|" in out else q)
    if act in {"left","right"}:
        time.sleep(0.15)
        _key_combo(["win",act])
    verbs={"maximize":"Maximized","minimize":"Minimized","restore":"Restored","close":"Close requested for","left":"Snapped left","right":"Snapped right"}
    return f"{verbs[act]} {title}."


def mouse_voice(action,dx=0,dy=0):
    """Small relative mouse controls for custom-drawn UI where accessibility labels are unavailable."""
    u=ctypes.windll.user32
    action=str(action or "").strip().lower()
    pt=ctypes.wintypes.POINT()
    if not u.GetCursorPos(ctypes.byref(pt)):
        raise RuntimeError("Could not read the mouse position.")
    if action=="move":
        nx=int(pt.x)+max(-2000,min(int(dx),2000))
        ny=int(pt.y)+max(-2000,min(int(dy),2000))
        u.SetCursorPos(nx,ny)
        return f"Moved the mouse to {nx}, {ny}."
    if action=="drag":
        nx=int(pt.x)+max(-2000,min(int(dx),2000))
        ny=int(pt.y)+max(-2000,min(int(dy),2000))
        u.mouse_event(0x0002,0,0,0,0)
        steps=12
        for i in range(1,steps+1):
            x=int(pt.x+(nx-pt.x)*i/steps)
            y=int(pt.y+(ny-pt.y)*i/steps)
            u.SetCursorPos(x,y)
            time.sleep(0.025)
        u.mouse_event(0x0004,0,0,0,0)
        return f"Dragged the mouse to {nx}, {ny}."
    if action=="click":
        u.mouse_event(0x0002,0,0,0,0); u.mouse_event(0x0004,0,0,0,0)
        return "Clicked."
    if action=="double":
        for _ in range(2):
            u.mouse_event(0x0002,0,0,0,0); u.mouse_event(0x0004,0,0,0,0); time.sleep(0.08)
        return "Double clicked."
    if action=="right":
        u.mouse_event(0x0008,0,0,0,0); u.mouse_event(0x0010,0,0,0,0)
        return "Right clicked."
    raise ValueError("Unsupported mouse action.")

def ui_set_toggle(label, desired):
    needle=str(label or "").strip()
    want=str(desired or "").strip().lower()
    if want not in {"on","off"}:
        raise ValueError("Toggle state must be on or off.")
    if not needle:
        raise ValueError("A toggle control name is required.")
    _,_,title=_foreground_window_info()
    helper=Path(__file__).resolve().parent/"uia_named_invoke.ps1"
    if not helper.exists():
        raise RuntimeError("UI automation helper is missing.")
    cmd=[
        "powershell.exe","-NoProfile","-ExecutionPolicy","Bypass",
        "-File",str(helper),
        "-WindowQuery",title,
        "-Label",needle,
        "-DesiredState",want,
    ]
    cp=subprocess.run(
        cmd,capture_output=True,text=True,timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if cp.returncode:
        raise RuntimeError((cp.stderr or cp.stdout or "Toggle action failed.").strip())
    return f"Set {needle} {want}."


def ui_invoke_named_window(window_query,label,control_type=""):
    helper=Path(__file__).resolve().parent/"uia_named_invoke.ps1"
    if not helper.exists():
        raise RuntimeError("Named-window UI helper is missing.")
    cmd=[
        "powershell.exe","-NoProfile","-ExecutionPolicy","Bypass",
        "-File",str(helper),
        "-WindowQuery",str(window_query or ""),
        "-Label",str(label or ""),
    ]
    if control_type:
        cmd += ["-ControlType",str(control_type)]
    cp=subprocess.run(
        cmd,capture_output=True,text=True,timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if cp.returncode:
        raise RuntimeError((cp.stderr or cp.stdout or "Named-window UI action failed.").strip())
    try:
        data=json.loads(cp.stdout.strip().splitlines()[-1])
        actual=str(data.get("Control") or label)
    except Exception:
        actual=str(label)
    return f"Activated {actual}."


def _run_uia_helper(label="", action="invoke", control_type="", desired="", value="", window_query=""):
    """Run the shared Windows UI Automation helper and return its JSON result."""
    helper=Path(__file__).resolve().parent/"uia_named_invoke.ps1"
    if not helper.exists():
        raise RuntimeError("UI automation helper is missing.")
    cmd=[
        "powershell.exe","-NoProfile","-ExecutionPolicy","Bypass",
        "-File",str(helper),
        "-WindowQuery",str(window_query or ""),
        "-Label",str(label or ""),
        "-Action",str(action or "invoke"),
    ]
    if control_type:
        cmd += ["-ControlType",str(control_type)]
    if desired:
        cmd += ["-DesiredState",str(desired)]
    if value != "":
        cmd += ["-Value",str(value)]
    cp=subprocess.run(cmd,capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if cp.returncode:
        raise RuntimeError((cp.stderr or cp.stdout or f"UI Automation {action} failed.").strip())
    lines=(cp.stdout or "").strip().splitlines()
    if not lines:
        raise RuntimeError("UI Automation returned no result.")
    try:
        return json.loads(lines[-1])
    except Exception:
        raise RuntimeError((cp.stdout or cp.stderr or "UI Automation returned invalid output.")[-1200:])

def ui_control(label="", operation="invoke", value="", desired="", control_type="", window_query=""):
    """Generic UI Automation layer for normal buttons, fields, menus, lists and toggles."""
    op=str(operation or "invoke").strip().lower()
    if op not in {"invoke","toggle","set_text","select","expand","collapse","focus","read"}:
        raise ValueError("Unsupported UI Automation operation.")
    if op not in {"set_text","select"} and not str(label or "").strip():
        raise ValueError("A control label is required.")
    if op=="toggle" and str(desired or "").lower() not in {"on","off",""}:
        raise ValueError("Toggle state must be on or off.")
    data=_run_uia_helper(
        label=str(label or "").strip(),
        action=op,
        control_type=str(control_type or "").strip(),
        desired=str(desired or "").strip().lower(),
        value=str(value or ""),
        window_query=str(window_query or "").strip(),
    )
    actual=str(data.get("Control") or label or "control")
    if op=="set_text":
        return f"Entered text in {actual}.",data
    if op=="toggle":
        state=str(data.get("State") or desired or "").lower()
        return f"Set {actual} {state or 'to the requested state'}.",data
    if op=="select":
        chosen=str(data.get("Value") or value or "").strip()
        return f"Selected {chosen or actual}.",data
    if op=="read":
        return f"Read {actual}.",data
    return f"Activated {actual}.",data


# ---- Universal visual + native control helpers ----

def _screen_image(max_width=1600):
    img=ImageGrab.grab(all_screens=True).convert("RGB")
    native=img.size
    if max_width and img.width>int(max_width):
        nh=max(1,int(img.height*int(max_width)/img.width))
        img=img.resize((int(max_width),nh))
    return img,native

def screen_png(max_width=1600):
    img,native=_screen_image(max_width)
    buf=io.BytesIO()
    img.save(buf,format="PNG",optimize=True)
    raw=buf.getvalue()
    return raw,native[0],native[1],img.width,img.height

def screen_fingerprint(max_width=640):
    img,native=_screen_image(max_width)
    gray=img.convert("L").resize((64,36))
    raw=gray.tobytes()
    return {
        "sha256":hashlib.sha256(raw).hexdigest(),
        "native_width":native[0],"native_height":native[1],
        "sample_width":64,"sample_height":36,
        "mean":round(sum(raw)/max(len(raw),1),2),
    }

def _tesseract_exe():
    candidates=[
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    found=shutil.which("tesseract")
    return Path(found) if found else None

def screen_ocr(max_width=1600,min_conf=35,limit=260):
    """Read text directly from screen pixels and return text plus normalized boxes."""
    exe=_tesseract_exe()
    if not exe:
        return {"available":False,"error":"Tesseract OCR is not installed.","text":"","words":[],"lines":[]}
    img,native=_screen_image(max_width)
    fd,path=tempfile.mkstemp(prefix="shal-screen-",suffix=".png",dir=str(RUNTIME))
    os.close(fd)
    try:
        img.save(path,format="PNG")
        cp=subprocess.run(
            [str(exe),path,"stdout","--psm","6","tsv"],
            capture_output=True,text=True,encoding="utf-8",errors="replace",
            timeout=35,creationflags=CREATE_NO_WINDOW
        )
        if cp.returncode:
            return {"available":True,"error":(cp.stderr or cp.stdout)[-1200:],"text":"","words":[],"lines":[]}
        rows=list(csv.DictReader(io.StringIO(cp.stdout),delimiter="\t"))
        words=[]
        groups={}
        iw,ih=img.size
        for row in rows:
            txt=str(row.get("text") or "").strip()
            if not txt:
                continue
            try: conf=float(row.get("conf","-1"))
            except: conf=-1
            if conf < float(min_conf):
                continue
            try:
                left=int(row.get("left",0)); top=int(row.get("top",0))
                width=int(row.get("width",0)); height=int(row.get("height",0))
            except:
                continue
            item={
                "text":txt,"conf":round(conf,1),
                "left":left,"top":top,"width":width,"height":height,
                "x":round((left+width/2)/max(iw,1),6),
                "y":round((top+height/2)/max(ih,1),6),
                "w":round(width/max(iw,1),6),
                "h":round(height/max(ih,1),6),
            }
            words.append(item)
            key=(row.get("block_num"),row.get("par_num"),row.get("line_num"))
            groups.setdefault(key,[]).append(item)
            if len(words)>=int(limit):
                break
        lines=[]
        for items in groups.values():
            if not items: continue
            text=" ".join(x["text"] for x in items).strip()
            if not text: continue
            l=min(x["left"] for x in items); t=min(x["top"] for x in items)
            r=max(x["left"]+x["width"] for x in items); b=max(x["top"]+x["height"] for x in items)
            lines.append({
                "text":text,
                "x":round(((l+r)/2)/max(iw,1),6),
                "y":round(((t+b)/2)/max(ih,1),6),
                "w":round((r-l)/max(iw,1),6),
                "h":round((b-t)/max(ih,1),6),
                "conf":round(sum(x["conf"] for x in items)/len(items),1)
            })
        full="\n".join(x["text"] for x in lines)
        return {
            "available":True,"error":"",
            "text":full[:16000],"words":words[:int(limit)],"lines":lines[:140],
            "capture_width":iw,"capture_height":ih,
            "native_width":native[0],"native_height":native[1],
            "fingerprint":hashlib.sha256(img.resize((64,36)).convert("L").tobytes()).hexdigest()
        }
    finally:
        try: os.unlink(path)
        except: pass

def find_screen_text(query,ocr=None):
    q=re.sub(r"\s+"," ",str(query or "").strip().lower())
    if not q:
        raise ValueError("Screen text is required.")
    ocr=ocr or screen_ocr()
    if not ocr.get("available"):
        raise RuntimeError(ocr.get("error") or "Screen OCR is unavailable.")
    candidates=[]
    for kind,items in (("line",ocr.get("lines") or []),("word",ocr.get("words") or [])):
        for item in items:
            txt=re.sub(r"\s+"," ",str(item.get("text") or "").strip().lower())
            if not txt: continue
            score=0.0
            if txt==q: score=1.0
            elif q in txt: score=0.94
            elif txt in q and len(txt)>=3: score=0.78
            else: score=difflib.SequenceMatcher(None,q,txt).ratio()
            if kind=="line" and " " in q: score+=0.03
            candidates.append((min(score,1.0),item))
    if not candidates:
        return None
    score,item=max(candidates,key=lambda x:x[0])
    if score<0.56:
        return None
    out=dict(item); out["match_score"]=round(score,3)
    return out

def click_screen_text(query,kind="click"):
    ocr=screen_ocr()
    hit=find_screen_text(query,ocr)
    if not hit:
        raise RuntimeError(f"I could not visually find '{query}' on the current screen.")
    data=pointer_action(hit["x"],hit["y"],kind if kind in {"click","double","right"} else "click")
    data.update({"matched_text":hit.get("text",""),"match_score":hit.get("match_score",0)})
    return data

def native_list_folder(path,limit=120):
    p=Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"Folder not found: {p}")
    rows=[]
    for child in sorted(p.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower()))[:int(limit)]:
        try:
            st=child.stat()
            rows.append({
                "name":child.name,"path":str(child),
                "type":"folder" if child.is_dir() else "file",
                "size":0 if child.is_dir() else st.st_size,
                "modified":int(st.st_mtime)
            })
        except Exception:
            rows.append({"name":child.name,"path":str(child),"type":"unknown"})
    return {"path":str(p),"items":rows}

def native_find_files(root,query,limit=80,max_depth=8):
    rootp=Path(os.path.expandvars(os.path.expanduser(str(root)))).resolve()
    if not rootp.exists() or not rootp.is_dir():
        raise FileNotFoundError(f"Folder not found: {rootp}")
    q=str(query or "").strip().lower()
    if not q:
        raise ValueError("A file search query is required.")
    hits=[]
    base_depth=len(rootp.parts)
    for cur,dirs,files in os.walk(rootp):
        depth=len(Path(cur).parts)-base_depth
        if depth>=int(max_depth):
            dirs[:]=[]
        for name in list(dirs)+list(files):
            if q in name.lower():
                path=Path(cur)/name
                try:
                    st=path.stat()
                    hits.append({
                        "name":name,"path":str(path),
                        "type":"folder" if path.is_dir() else "file",
                        "size":0 if path.is_dir() else st.st_size,
                        "modified":int(st.st_mtime)
                    })
                except Exception:
                    hits.append({"name":name,"path":str(path),"type":"unknown"})
                if len(hits)>=int(limit):
                    return {"root":str(rootp),"query":q,"items":hits}
    return {"root":str(rootp),"query":q,"items":hits}

def native_new_folder(path):
    p=Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
    p.mkdir(parents=True,exist_ok=False)
    return {"path":str(p)}

def native_copy_move(src,dst,move=False,overwrite=False):
    s=Path(os.path.expandvars(os.path.expanduser(str(src)))).resolve()
    d=Path(os.path.expandvars(os.path.expanduser(str(dst)))).resolve()
    if not s.exists():
        raise FileNotFoundError(f"Source not found: {s}")
    target=d/s.name if d.exists() and d.is_dir() else d
    if target.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {target}")
    target.parent.mkdir(parents=True,exist_ok=True)
    if move:
        shutil.move(str(s),str(target))
    elif s.is_dir():
        shutil.copytree(str(s),str(target),dirs_exist_ok=bool(overwrite))
    else:
        shutil.copy2(str(s),str(target))
    return {"source":str(s),"destination":str(target),"operation":"move" if move else "copy"}

def recycle_path(path):
    p=Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {p}")
    safe=str(p).replace("'","''")
    if p.is_dir():
        script=f"""Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('{safe}','OnlyErrorDialogs','SendToRecycleBin')
'OK'"""
    else:
        script=f"""Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{safe}','OnlyErrorDialogs','SendToRecycleBin')
'OK'"""
    rc,out,err=run_ps(script,30)
    if rc or "OK" not in out:
        raise RuntimeError(err or out or "Recycle operation failed.")
    return {"path":str(p),"recycled":True}

def native_processes(limit=120):
    script=r"""Get-Process | Sort-Object ProcessName | Select-Object -First 160 ProcessName,Id,CPU,WorkingSet64,MainWindowTitle | ConvertTo-Json -Compress"""
    rc,out,err=run_ps(script,20)
    if rc: raise RuntimeError(err or out)
    try: data=json.loads(out or "[]")
    except: data=[]
    if isinstance(data,dict): data=[data]
    return data[:int(limit)]

def native_close_process(query,force=False):
    q=str(query or "").strip()
    if not q: raise ValueError("A process or window name is required.")
    safe=q.replace("'","''")
    script=f"""$p=Get-Process | Where-Object {{$_.ProcessName -like '*{safe}*' -or $_.MainWindowTitle -like '*{safe}*'}} | Select-Object -First 1
if(-not $p){{throw "Process not found"}}
if({str(bool(force)).lower()}){{$p | Stop-Process -Force}}else{{
  if($p.MainWindowHandle -ne 0){{$null=$p.CloseMainWindow()}}else{{throw "No closable window"}}
}}
"$($p.ProcessName)|$($p.Id)" """
    rc,out,err=run_ps(script,20)
    if rc: raise RuntimeError(err or out or "Could not close the process.")
    return {"closed":out.strip(),"force":bool(force)}

def native_service_action(name,action="status"):
    svc=str(name or "").strip()
    act=str(action or "status").strip().lower()
    if not svc: raise ValueError("A service name is required.")
    if act not in {"status","start","stop","restart"}: raise ValueError("Unsupported service action.")
    safe=svc.replace("'","''")
    if act=="status":
        script=f"""$s=Get-Service -Name '{safe}' -ErrorAction Stop
[pscustomobject]@{{Name=$s.Name;DisplayName=$s.DisplayName;Status=$s.Status.ToString();StartType=$s.StartType.ToString()}} | ConvertTo-Json -Compress"""
    elif act=="start":
        script=f"""Start-Service -Name '{safe}' -ErrorAction Stop; (Get-Service -Name '{safe}') | Select-Object Name,Status | ConvertTo-Json -Compress"""
    elif act=="stop":
        script=f"""Stop-Service -Name '{safe}' -ErrorAction Stop; (Get-Service -Name '{safe}') | Select-Object Name,Status | ConvertTo-Json -Compress"""
    else:
        script=f"""Restart-Service -Name '{safe}' -ErrorAction Stop; (Get-Service -Name '{safe}') | Select-Object Name,Status | ConvertTo-Json -Compress"""
    rc,out,err=run_ps(script,35)
    if rc: raise RuntimeError(err or out or f"Service {act} failed.")
    try: return json.loads(out)
    except: return {"result":out}

def native_network_status():
    script=r"""$a=Get-NetAdapter -ErrorAction SilentlyContinue | Select-Object Name,InterfaceDescription,Status,LinkSpeed,MacAddress,ifIndex
$ip=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {$_.IPAddress -notlike '169.254*'} | Select-Object InterfaceAlias,IPAddress,PrefixLength
[pscustomobject]@{Adapters=$a;IPv4=$ip} | ConvertTo-Json -Depth 5 -Compress"""
    rc,out,err=run_ps(script,25)
    if rc: raise RuntimeError(err or out or "Network status unavailable.")
    try:return json.loads(out)
    except:return {"raw":out}

def clipboard_get_text():
    rc,out,err=run_ps("Get-Clipboard -Raw -TextFormatType Text -ErrorAction SilentlyContinue",10)
    if rc: raise RuntimeError(err or out)
    return out[:12000]

def clipboard_set_text(text):
    value=str(text or "")
    safe=value.replace("'@","' + '@")
    script=f"""Set-Clipboard -Value @'
{safe}
'@
'OK'"""
    rc,out,err=run_ps(script,10)
    if rc: raise RuntimeError(err or out)
    return {"chars":len(value)}
