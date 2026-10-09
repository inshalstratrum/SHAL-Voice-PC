import os, re, json, time, uuid, sqlite3, secrets, subprocess, ctypes, socket, webbrowser, base64, threading
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Any
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel
import desktop_bridge as bridge

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
DB_PATH = BASE / "state.db"
LOG_PATH = BASE / "logs" / "voice-agent.log"
OMNI_ENV_PATH = Path(r"E:\00 - Master Tools & Switch Board\omni route\.env")
LOG_PATH.parent.mkdir(exist_ok=True)
PENDING = {}
ACTIVE_TASKS = {}
TASK_LOCK = threading.Lock()
# Vision model verified through the local OmniRoute provider on SHAL.
# Keep this separate from the text "auto" route so every autonomous step can
# inspect actual screenshot pixels, not only accessibility/OCR text.
VISION_MODEL = "qwen/qwen3.8-omni-flash"
UNIVERSAL_MAX_STEPS = 16

DEFAULT_CONFIG = {
    "port": 8765,
    "pairing_key": "",
    "apps": {
        "chrome": {"kind":"exe","value":"chrome.exe","title":"Chrome","process":"chrome"},
        "obsidian": {"kind":"shell","value":"md.obsidian","title":"Obsidian","process":"Obsidian"},
        "antigravity": {"kind":"exe","value":r"C:\Users\ROC STORE\AppData\Local\Programs\antigravity\Antigravity.exe","title":"Antigravity","process":"Antigravity"},
        "chatgpt": {"kind":"shell","value":"OpenAI.Codex_2p2nqsd0c76g0!App","title":"ChatGPT","process":"ChatGPT"},
        "codex": {"kind":"shell","value":"OpenAI.Codex_2p2nqsd0c76g0!App","title":"ChatGPT","process":"ChatGPT"}
    }
}
def now():
    return datetime.now().isoformat(timespec="seconds")

def log(message):
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{now()}] {message}\n")

def load_config():
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    else:
        cfg = DEFAULT_CONFIG.copy()
    if not cfg.get("pairing_key"):
        cfg["pairing_key"] = secrets.token_urlsafe(32)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg

CONFIG = load_config()

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, user_text TEXT, action TEXT,
        result TEXT, success INTEGER NOT NULL DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS agent_state(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    con.commit()
    return con

def event(user_text, action, result, success=True):
    with db() as con:
        con.execute(
            "INSERT INTO events(ts,user_text,action,result,success) VALUES(?,?,?,?,?)",
            (now(), user_text, action, result, 1 if success else 0)
        )
        con.commit()

def set_state(key, value):
    value = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    with db() as con:
        con.execute(
            "INSERT INTO agent_state(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, value, now())
        )
        con.commit()

def get_state(key, default=None):
    with db() as con:
        row=con.execute("SELECT value FROM agent_state WHERE key=?",(key,)).fetchone()
    if not row:
        return default
    value=row[0]
    try:
        return json.loads(value)
    except:
        return value

def recent_context(limit=8):
    with db() as con:
        rows=con.execute(
            "SELECT user_text,action,result,success FROM events ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    rows=list(reversed(rows))
    return [
        {"user":r[0],"action":r[1],"result":r[2],"success":bool(r[3])}
        for r in rows
    ]
def run_ps(script, timeout=30):
    cp = subprocess.run(
        ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    return cp.returncode, cp.stdout.strip(), cp.stderr.strip()

def get_tailscale_ip():
    try:
        cp = subprocess.run(
            ["tailscale","ip","-4"], capture_output=True, text=True,
            timeout=5, creationflags=subprocess.CREATE_NO_WINDOW
        )
        ip = cp.stdout.strip().splitlines()
        return ip[0] if ip else "127.0.0.1"
    except:
        return "127.0.0.1"

def service_state(name):
    rc,out,err = run_ps(
        f"""$s=Get-CimInstance Win32_Service -Filter "Name='{name}'" -ErrorAction SilentlyContinue
if(-not $s){{'NOT_FOUND'}}else{{"$($s.State)|$($s.StartMode)|$($s.ProcessId)"}}"""
    )
    if not out or out == "NOT_FOUND":
        return {"state":"Not found","startup":"-","pid":0}
    parts=(out.split("|")+["-","0"])[:3]
    try: pid=int(parts[2])
    except: pid=0
    return {"state":parts[0],"startup":parts[1],"pid":pid}
def system_status():
    script = r"""
$os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average
$used=[math]::Round(($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/1MB,1)
$total=[math]::Round($os.TotalVisibleMemorySize/1MB,1)
[pscustomobject]@{
 Computer=$env:COMPUTERNAME
 CPU=[math]::Round($cpu.Average,0)
 RAMUsedGB=$used
 RAMTotalGB=$total
 UptimeMinutes=[math]::Round(((Get-Date)-$os.LastBootUpTime).TotalMinutes,0)
} | ConvertTo-Json -Compress
"""
    rc,out,err=run_ps(script)
    try: core=json.loads(out)
    except: core={"error":err or out or "status unavailable"}
    core["tailscale"]=service_state("Tailscale")
    core["rustdesk"]=service_state("RustDesk")
    core["openssh"]=service_state("sshd")
    core["tailscale_ip"]=get_tailscale_ip()
    return core

def launch_app(target):
    app=CONFIG["apps"].get(target)
    if not app:
        raise ValueError(f"Unknown app: {target}")
    if app["kind"]=="exe":
        subprocess.Popen([app["value"]], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    elif app["kind"]=="shell":
        run_ps(f"Start-Process 'shell:AppsFolder\\{app['value']}'")
    elif app["kind"]=="terminal":
        subprocess.Popen(
            ["wt.exe","new-tab","powershell.exe","-NoExit","-Command",app["value"]],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
    return f"Opened {target}."
def focus_title(title):
    safe=title.replace("'","''")
    script=f"""$w=New-Object -ComObject WScript.Shell
if($w.AppActivate('{safe}')){{'OK'}}else{{'NO'}}"""
    rc,out,err=run_ps(script)
    return out.strip()=="OK"

def paste_and_submit(title, text):
    if not focus_title(title):
        return False, f"Could not focus {title}."
    encoded = json.dumps(text)
    script = f"""$text = ConvertFrom-Json @'
{encoded}
'@
Set-Clipboard -Value $text
Start-Sleep -Milliseconds 250
$w=New-Object -ComObject WScript.Shell
$w.SendKeys('^v')
Start-Sleep -Milliseconds 150
$w.SendKeys('{{ENTER}}')
'OK'"""
    rc,out,err=run_ps(script, timeout=15)
    return rc==0 and "OK" in out, err or out

def prompt_app(target, prompt):
    app=CONFIG["apps"].get(target)
    if not app:
        raise ValueError(f"Unknown app: {target}")
    launch_app(target)
    time.sleep(2.0)
    ok,msg=paste_and_submit(app["title"], prompt)
    if not ok:
        return f"Opened {target}, but automatic prompt entry failed: {msg}"
    return f"Opened {target} and submitted the prompt."

def browser_search(query):
    import urllib.parse
    url="https://www.google.com/search?q="+urllib.parse.quote(query)
    if not webbrowser.open(url,new=2):
        raise RuntimeError("Windows could not open the default browser.")
    return f"Opened a browser search for: {query}"

def browser_open_url(value):
    raw=str(value or "").strip()
    if not raw:
        raise ValueError("A website address is required.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://",raw,re.I):
        raw="https://"+raw
    from urllib.parse import urlparse
    u=urlparse(raw)
    if u.scheme not in {"http","https"} or not u.netloc:
        raise ValueError("Only normal HTTP or HTTPS website addresses are allowed.")
    webbrowser.open(raw,new=2)
    return f"Opened {u.netloc}."

def active_window_text(limit=14000):
    script = r"""
$code=@'
using System;
using System.Runtime.InteropServices;
public class FGVA {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
'@
Add-Type $code -ErrorAction SilentlyContinue
Add-Type -AssemblyName UIAutomationClient
$h=[FGVA]::GetForegroundWindow()
$root=[System.Windows.Automation.AutomationElement]::FromHandle($h)
"WINDOW: $($root.Current.Name)"
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
foreach($e in $all){
  $n=$e.Current.Name
  if($n -and $n.Length -gt 1){$n}
}
"""
    rc,out,err=run_ps(script, timeout=20)
    text=out or err
    if len(text)>limit:
        text=text[-limit:]
    return text.strip()

def launch_app(target):
    app=CONFIG["apps"].get(target)
    if not app:
        raise ValueError(f"Unknown app: {target}")
    return bridge.launch_app(target,app)

def prompt_app(target,prompt):
    app=CONFIG["apps"].get(target)
    if not app:
        raise ValueError(f"Unknown app: {target}")
    return bridge.prompt_app(target,app,prompt)

def active_window_text(limit=16000):
    return bridge.active_window_text(limit)

def target_last_response(target):
    app=CONFIG["apps"].get(target)
    if not app:
        raise ValueError(f"Unknown app: {target}")
    return bridge.latest_response(target,app)

ANSI_RE=re.compile(r"\x1b\[[0-9;]*m")

def omni_env():
    env=os.environ.copy()
    omni_data=str(OMNI_ENV_PATH.parent)
    env["DATA_DIR"]=omni_data
    env["OMNIROUTE_DATA_DIR"]=omni_data
    # OmniRoute loads DATA_DIR\.env itself. Do not duplicate secrets in the
    # process environment because the CLI treats duplicate key sources as an error.
    return env

def omni_server_online():
    try:
        with socket.create_connection(("127.0.0.1",20128),timeout=1.5):
            return True
    except OSError:
        return False

def ensure_omniroute_server():
    if omni_server_online():
        return True
    env=omni_env()
    env["OMNIROUTE_SERVER_HOST"]="127.0.0.1"
    cp=subprocess.run(
        ["cmd.exe","/d","/c","omniroute","serve","--daemon","--no-open","--no-tray"],
        cwd=str(OMNI_ENV_PATH.parent),env=env,
        capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    for _ in range(20):
        if omni_server_online():
            return True
        time.sleep(0.5)
    log("OmniRoute start failed: "+(cp.stderr or cp.stdout or "unknown"))
    return False

def omni_chat(prompt, system, max_tokens=500):
    if not ensure_omniroute_server():
        raise RuntimeError("OmniRoute server is unavailable.")
    payload={
        "model":"auto",
        "messages":[
            {"role":"system","content":system},
            {"role":"user","content":prompt}
        ],
        "max_tokens":int(max_tokens),
        "temperature":0.1
    }
    r=requests.post(
        "http://127.0.0.1:20128/v1/chat/completions",
        json=payload,timeout=90
    )
    if r.status_code>=400:
        raise RuntimeError(f"OmniRoute HTTP {r.status_code}: {r.text[:500]}")
    data=r.json()
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except Exception:
        raise RuntimeError("OmniRoute returned an unexpected response.")

def summarize_text(text, request="Summarize this for spoken playback."):
    if not text:
        return "I could not read useful text from the active window."
    system=("You summarize screen text for natural spoken playback. Be concise and semantic. "
            "Preserve decisions, errors, commands, and next actions. Do not invent missing information. "
            "Output plain conversational sentences only. Do not use markdown, code fences, bullets, "
            "backticks, bracket notation, slash-heavy paths, or punctuation that would sound robotic.")
    return bridge.clean_for_speech(omni_chat(request+"\n\nSCREEN TEXT:\n"+text, system, 450))

def summarize_target_response(target):
    text=target_last_response(target)
    label="Antigravity" if target=="antigravity" else ("Codex" if target=="codex" else "ChatGPT")
    summary=summarize_text(
        text,
        f"Summarize only the latest assistant response from {label}. Explain the result and next action naturally for speech."
    )
    set_state("last_summary",summary)
    set_state("last_target",target)
    return summary

def send_active_summary_to_app(target, instruction="Use this context and continue the work."):
    text=active_window_text()
    if not text:
        return "I could not read useful text from the active window."
    summary=summarize_text(
        text,
        "Create a compact handoff of the important content, latest response, decisions, commands, errors, and next action."
    )
    set_state("last_summary",summary)
    set_state("last_target",target)
    payload=f"{instruction}\n\nContext captured from the active PC window:\n{summary}"
    return prompt_app(target,payload)

def send_last_context_to_app(target, instruction="Use this context and continue the work."):
    summary=get_state("last_summary","")
    if not summary:
        with db() as con:
            row=con.execute(
                "SELECT result FROM events WHERE success=1 AND result IS NOT NULL AND result<>'' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        summary=row[0] if row else ""
    if not summary:
        return "I do not have a saved response or summary to send yet."
    set_state("last_target",target)
    payload=f"{instruction}\n\nSaved context from the voice agent:\n{summary}"
    return prompt_app(target,payload)

def lock_windows():
    rc,out,err=run_ps("rundll32.exe user32.dll,LockWorkStation",timeout=10)
    if rc:
        raise RuntimeError(err or out or "Windows lock failed")
    return "Windows is locking now."

def sleep_windows():
    script=r"""
$code=@'
using System;
using System.Runtime.InteropServices;
public class PowrProfVA {
  [DllImport("PowrProf.dll", SetLastError=true)]
  public static extern bool SetSuspendState(bool hibernate, bool forceCritical, bool disableWakeEvent);
}
'@
Add-Type $code -ErrorAction SilentlyContinue
if(-not [PowrProfVA]::SetSuspendState($false,$false,$false)){
  throw "Windows refused the sleep request."
}
"""
    rc,out,err=run_ps(script,timeout=20)
    if rc:
        raise RuntimeError(err or out or "Sleep failed")
    return "The PC is entering sleep."

def get_sleep_settings():
    rc,out,err=run_ps("""$a=(powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String 'Current AC Power Setting Index').Line
$d=(powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String 'Current DC Power Setting Index').Line
Write-Output ("AC="+$a)
Write-Output ("DC="+$d)""")
    return out or err

def set_ac_sleep_minutes(minutes):
    minutes=max(0,min(int(minutes),1440))
    cp=subprocess.run(
        ["powercfg.exe","/change","standby-timeout-ac",str(minutes)],
        capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW
    )
    if cp.returncode:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "Could not change sleep timeout")
    return f"AC sleep timeout set to {'Never' if minutes==0 else str(minutes)+' minutes'}."
def parse_json_object(text):
    text=ANSI_RE.sub("",text)
    start=text.find("{"); end=text.rfind("}")
    if start<0 or end<=start:
        raise ValueError("No JSON object returned")
    return json.loads(text[start:end+1])

def visible_control_name(query):
    q=re.sub(r"[^a-z0-9]+"," ",str(query or "").lower()).strip()
    if not q:
        return ""
    try:
        snap=bridge.ui_snapshot(90)
    except Exception:
        return ""
    allowed_types={"Button","TabItem","ListItem","Hyperlink","CheckBox","RadioButton","MenuItem","TreeItem"}
    best=None
    best_score=999
    q_words=set(q.split())
    for e in snap.get("elements") or []:
        if e.get("offscreen") or not e.get("enabled",True):
            continue
        if e.get("type") not in allowed_types:
            continue
        name=str(e.get("name") or "").strip()
        if not name:
            continue
        lname=name.lower()
        if lname.startswith(("minimize ","maximize ","restore ","close ")) and not q.startswith(("minimize ","maximize ","restore ","close ")):
            continue
        n=re.sub(r"[^a-z0-9]+"," ",lname).strip()
        if not n:
            continue
        score=999
        if n==q:
            score=0
        elif n.startswith(q) or q.startswith(n):
            score=1
        elif q in n:
            score=2
        else:
            n_words=set(n.split())
            if q_words and q_words.issubset(n_words):
                score=3
        if score<best_score:
            best_score=score
            best=name
    return best or ""

def describe_ui(brief=False):
    snap=bridge.ui_snapshot(80)
    window=str(snap.get("window") or "the active window").strip()
    process=str(snap.get("process") or "").strip()
    if brief:
        if process:
            return f"You are in {window}, running in {process}."
        return f"You are in {window}."
    names=[]
    seen=set()
    allowed={"Button","TabItem","ListItem","Hyperlink","CheckBox","RadioButton","MenuItem","TreeItem","Edit","ComboBox"}
    for e in snap.get("elements") or []:
        if e.get("offscreen") or not e.get("enabled",True) or e.get("type") not in allowed:
            continue
        name=str(e.get("name") or "").strip()
        if not name:
            continue
        key=name.lower()
        if key in seen:
            continue
        if key in {"minimize","maximize","restore","close"}:
            continue
        seen.add(key)
        names.append(name)
        if len(names)>=12:
            break
    if names:
        return bridge.clean_for_speech(f"You are in {window}. Visible controls include " + ", ".join(names) + ".")
    return bridge.clean_for_speech(f"You are in {window}. I could not find clearly named controls on this screen.")

def local_rule(user_text, allow_sequence=True):
    t=user_text.strip()
    low=t.lower().replace("anti gravity","antigravity").replace("chat gpt","chatgpt")
    if allow_sequence:
        parts=[p.strip(" ,.;") for p in re.split(r"\s+(?:and\s+then|then)\s+",t,flags=re.I) if p.strip(" ,.;")]
        if 1 < len(parts) <= 8:
            return {"reply":f"I will do those {len(parts)} steps in order.","action":{"name":"voice_sequence","args":{"commands":parts}}}
    aliases={
        "google chrome":"chrome","chrome":"chrome","browser":"chrome",
        "obsidian":"obsidian","antigravity":"antigravity",
        "chatgpt":"chatgpt","chat gpt":"chatgpt","codex":"codex"
    }
    m=re.match(r"^(?:please )?open (.+?)[.!]?$",low)
    if m and m.group(1) in aliases:
        return {"reply":f"Opening {m.group(1)}.","action":{"name":"open_app","args":{"target":aliases[m.group(1)]}}}
    if any(x in low for x in ["pc status","system status","computer status","health status"]):
        return {"reply":"Checking PC status.","action":{"name":"status","args":{}}}
    if low.startswith("search ") or "search the web for " in low:
        q=re.sub(r"^(search( the web)? for|search)\s+","",t,flags=re.I)
        return {"reply":"Opening the browser search.","action":{"name":"browser_search","args":{"query":q}}}
    for target in ("antigravity","codex","chatgpt"):
        if target in low and any(x in low for x in ["last response","last reply","latest response","latest reply"]):
            if any(x in low for x in ["summarize","summarise","read","check","tell me","what did"]):
                return {"reply":f"I will summarize the latest {target} response.","action":{"name":"summarize_target","args":{"target":target}}}
        if target in low and any(x in low for x in ["screen","window","doing","working on","current response"]):
            if any(x in low for x in ["summarize","summarise","read","check","tell me","what is","what's","what did"]):
                return {"reply":f"I will check {target}.","action":{"name":"summarize_target","args":{"target":target}}}
        if target in low and any(x in low for x in ["send that","use that","continue that","last summary","saved context"]):
            return {"reply":f"I will send the saved context to {target}.","action":{"name":"send_last_context","args":{"target":target}}}
        if target in low and any(x in low for x in ["this chat","current response","current window"]):
            if any(x in low for x in ["send","use","continue","handoff","hand off"]):
                return {"reply":f"I will capture the active window and prepare a handoff for {target}.","action":{"name":"send_active_summary","args":{"target":target}}}
    if any(x in low for x in ["what is on my screen","what is on the screen","describe my screen","describe the screen","look at my screen","what error is showing","read this popup","what does this popup say"]):
        return {"reply":"Looking at the actual screen.","action":{"name":"screen_describe","args":{"request":t}}}
    if any(x in low for x in ["what can i do here","what options are here","what controls are here","what buttons are here","describe the controls","show available controls"]):
        return {"reply":"Checking the controls on this screen.","action":{"name":"describe_ui","args":{}}}
    if any(x in low for x in ["where am i","what window is this","which window is active","what app am i in"]):
        return {"reply":"Checking the active window.","action":{"name":"describe_ui","args":{"brief":True}}}
    if ("read" in low or "summarize" in low or "summarise" in low) and "screen" in low:
        return {"reply":"I will inspect the visible screen directly.","action":{"name":"screen_describe","args":{"request":t}}}
    if ("read" in low or "summarize" in low or "summarise" in low) and any(x in low for x in ["window","response","reply","chat"]):
        return {"reply":"I will read and summarize the active window.","action":{"name":"summarize_active","args":{}}}
    for target in ("antigravity","codex","chatgpt"):
        m=re.search(rf"(?:run|send|ask).*?{target}.*?(?:to|:)?\s+(.+)$",t,re.I)
        if m and len(m.group(1).strip())>3:
            return {"reply":f"Ready to send that to {target}.","action":{"name":"prompt_app","args":{"target":target,"prompt":m.group(1).strip()}}}
    if "restart rustdesk" in low:
        return {"reply":"Restarting RustDesk.","action":{"name":"restart_rustdesk","args":{}}}

    # General window control.
    for target in ("antigravity","codex","chatgpt","chrome","obsidian"):
        if target in low and any(x in low for x in ["maximize","maximise","expand"]):
            return {"reply":f"Maximizing {target}.","action":{"name":"app_window","args":{"target":target,"action":"maximize"}}}
        if target in low and any(x in low for x in ["minimize","minimise"]):
            return {"reply":f"Minimizing {target}.","action":{"name":"app_window","args":{"target":target,"action":"minimize"}}}
        if target in low and any(x in low for x in ["close","exit","quit"]):
            return {"reply":f"Closing {target} requires confirmation.","action":{"name":"app_window","args":{"target":target,"action":"close"}}}
        if target in low and any(x in low for x in ["switch to","go to","bring up","show me","focus","bring back","restore"]):
            return {"reply":f"Switching to {target}.","action":{"name":"focus_app","args":{"target":target}}}

    m=re.match(r"^(?:please )?(maximize|maximise|minimize|minimise|restore|close|snap left|snap right)\s+(.+?)[.!]?$",t,re.I)
    if m and m.group(2).strip().lower() not in {"this","this window","current","current window","active window","it","that","that window","all","all windows"}:
        verb=m.group(1).lower()
        action={"maximize":"maximize","maximise":"maximize","minimize":"minimize","minimise":"minimize",
                "restore":"restore","close":"close","snap left":"left","snap right":"right"}[verb]
        return {"reply":f"{verb.title()} {m.group(2).strip()}.","action":{"name":"named_window_action","args":{"query":m.group(2).strip(),"action":action}}}

    if any(x in low for x in ["restore all windows","bring back all windows","show all windows again","undo minimize all","undo minimise all"]):
        return {"reply":"Restoring minimized windows.","action":{"name":"window_action","args":{"action":"restore_all"}}}
    if any(x in low for x in ["maximize this window","maximise this window","maximize current window","maximise current window","expand this window","make this full screen","make this window full size","maximize it","maximise it","expand it"]):
        return {"reply":"Maximizing the active window.","action":{"name":"window_action","args":{"action":"maximize"}}}
    if any(x in low for x in ["restore this window","restore current window","unmaximize this window","unmaximise this window","normal size window","restore it","bring it back"]):
        return {"reply":"Restoring the active window.","action":{"name":"window_action","args":{"action":"restore"}}}
    if any(x in low for x in ["minimize this window","minimise this window","minimize current window","minimise current window","minimize it","minimise it"]):
        return {"reply":"Minimizing the active window.","action":{"name":"window_action","args":{"action":"minimize"}}}
    if any(x in low for x in ["close this folder","close current folder","close the folder"]):
        return {"reply":"Closing the active File Explorer window.","action":{"name":"explorer_action","args":{"action":"close"}}}
    if any(x in low for x in ["close this window","close current window","close active window","close the window","close it"]):
        return {"reply":"Closing the active window requires confirmation.","action":{"name":"window_action","args":{"action":"close"}}}
    if any(x in low for x in ["snap left","move window left","put window on left","left half"]):
        return {"reply":"Moving the active window to the left.","action":{"name":"window_action","args":{"action":"left"}}}
    if any(x in low for x in ["snap right","move window right","put window on right","right half"]):
        return {"reply":"Moving the active window to the right.","action":{"name":"window_action","args":{"action":"right"}}}
    if any(x in low for x in ["next window","switch window","switch to next window","alt tab"]):
        return {"reply":"Switching to the next window.","action":{"name":"window_action","args":{"action":"next"}}}
    if any(x in low for x in ["previous window","switch to previous window","last window"]):
        return {"reply":"Switching to the previous window.","action":{"name":"window_action","args":{"action":"previous"}}}
    if any(x in low for x in ["what windows are open","list open windows","show open windows","which windows are open"]):
        return {"reply":"Checking open windows.","action":{"name":"list_windows","args":{}}}

    if low in {"press enter","enter","hit enter"}:
        return {"reply":"Pressing Enter.","action":{"name":"keyboard_action","args":{"action":"enter"}}}
    if low in {"press escape","escape","hit escape","esc"}:
        return {"reply":"Pressing Escape.","action":{"name":"keyboard_action","args":{"action":"escape"}}}
    if low in {"press tab","tab","next field"}:
        return {"reply":"Pressing Tab.","action":{"name":"keyboard_action","args":{"action":"tab"}}}
    if any(x in low for x in ["select all","control a","ctrl a"]):
        return {"reply":"Selecting all.","action":{"name":"keyboard_action","args":{"action":"select_all"}}}
    if any(x in low for x in ["undo that","undo last action","control z","ctrl z"]):
        return {"reply":"Undoing the last action.","action":{"name":"keyboard_action","args":{"action":"undo"}}}
    if any(x in low for x in ["redo that","redo last action","control y","ctrl y"]):
        return {"reply":"Redoing the last action.","action":{"name":"keyboard_action","args":{"action":"redo"}}}
    if any(x in low for x in ["save this","save current","control s","ctrl s"]):
        return {"reply":"Saving.","action":{"name":"keyboard_action","args":{"action":"save"}}}
    if any(x in low for x in ["delete selected","delete this","press delete"]):
        return {"reply":"Deleting the selected item requires confirmation.","action":{"name":"keyboard_action","args":{"action":"delete"}}}
    m=re.match(r"^(?:please )?(?:type|enter|write)\s+(.+?)\s+(?:in|into)\s+(?:the\s+)?(.+?)(?:\s+and\s+press\s+enter)?[.!]?$",t,re.I)
    if m:
        submit=bool(re.search(r"and\s+press\s+enter[.!]?$",t,re.I))
        label=re.sub(r"\s+and\s+press\s+enter[.!]?$","",m.group(2),flags=re.I).strip()
        return {"reply":f"Entering text in {label}.","action":{"name":"ui_set_text","args":{"label":label,"text":m.group(1).strip(),"submit":submit}}}
    m=re.match(r"^(?:please )?set\s+(?:the\s+)?(.+?)\s+to\s+(.+?)(?:\s+and\s+press\s+enter)?[.!]?$",t,re.I)
    if m:
        submit=bool(re.search(r"and\s+press\s+enter[.!]?$",t,re.I))
        value=re.sub(r"\s+and\s+press\s+enter[.!]?$","",m.group(2),flags=re.I).strip()
        return {"reply":f"Changing {m.group(1).strip()}.","action":{"name":"ui_set_text","args":{"label":m.group(1).strip(),"text":value,"submit":submit}}}
    m=re.match(r"^(?:please )?(?:type|write)\s+(.+?)(?:\s+and\s+press\s+enter)?[.!]?$",t,re.I)
    if m:
        submit=bool(re.search(r"and\s+press\s+enter[.!]?$",t,re.I))
        text_to_type=re.sub(r"\s+and\s+press\s+enter[.!]?$","",m.group(1),flags=re.I).strip()
        return {"reply":"Typing that text.","action":{"name":"type_text","args":{"text":text_to_type,"submit":submit}}}

    if any(x in low for x in ["minimize all","minimise all","show desktop","clear the desktop"]) and any(x in low for x in ["open this pc","open my pc","open my computer","open computer folder"]):
        return {"reply":"Minimizing all windows and opening This PC.","action":{"name":"minimize_open_this_pc","args":{}}}
    if any(x in low for x in ["minimize all","minimise all","show desktop","clear the desktop"]):
        return {"reply":"Minimizing all windows.","action":{"name":"minimize_all","args":{}}}
    if any(x in low for x in ["open this pc","open my pc","open my computer","open computer folder"]):
        return {"reply":"Opening This PC.","action":{"name":"open_this_pc","args":{}}}
    m=re.search(r"open\s+(?:the\s+)?([a-z])\s*(?:drive|disk)\b",low)
    if m:
        return {"reply":f"Opening drive {m.group(1).upper()}.","action":{"name":"open_drive","args":{"letter":m.group(1).upper()}}}
    if any(x in low for x in ["open selected","open this folder","open this file","open this drive","open this","open that","enter this folder"]):
        return {"reply":"Opening the selected item.","action":{"name":"explorer_action","args":{"action":"open_selected"}}}
    if any(x in low for x in ["go back","back one folder","previous folder","back"]):
        return {"reply":"Going back.","action":{"name":"keyboard_action","args":{"action":"back"}}}
    if any(x in low for x in ["go forward","forward one folder","next folder in history","forward"]):
        return {"reply":"Going forward.","action":{"name":"keyboard_action","args":{"action":"forward"}}}
    if any(x in low for x in ["go up","up one folder","parent folder"]):
        return {"reply":"Going up one folder.","action":{"name":"explorer_action","args":{"action":"up"}}}
    if any(x in low for x in ["copy this file","copy selected","copy this folder"]):
        return {"reply":"Copying the selected item.","action":{"name":"explorer_action","args":{"action":"copy"}}}
    if any(x in low for x in ["cut this file","cut selected","cut this folder","move this file"]):
        return {"reply":"Cutting the selected item.","action":{"name":"keyboard_action","args":{"action":"cut"}}}
    if any(x in low for x in ["paste here","paste into this folder","paste this here"]):
        return {"reply":"Pasting into the current folder requires confirmation.","action":{"name":"explorer_action","args":{"action":"paste"}}}
    if any(x in low for x in ["refresh this folder","refresh folder","refresh window"]):
        return {"reply":"Refreshing.","action":{"name":"keyboard_action","args":{"action":"refresh"}}}
    m=re.search(r"(?:rename|change name of)\s+(?:this|selected|this file|this folder|selected file|selected folder)\s+(?:to|as)\s+(.+)$",t,re.I)
    if m:
        return {"reply":"Renaming the selected item.","action":{"name":"explorer_rename","args":{"name":m.group(1).strip()}}}
    m=re.search(r"(?:create|make|new)\s+(?:a\s+)?folder(?:\s+(?:called|named))?\s+(.+)$",t,re.I)
    if m:
        return {"reply":"Creating the folder.","action":{"name":"explorer_new_folder","args":{"name":m.group(1).strip()}}}
    if any(x in low for x in ["select next","next item","next file","next folder"]):
        return {"reply":"Selecting the next item.","action":{"name":"explorer_action","args":{"action":"next"}}}
    if any(x in low for x in ["select previous","previous item","previous file","previous folder"]):
        return {"reply":"Selecting the previous item.","action":{"name":"explorer_action","args":{"action":"previous"}}}
    if "scroll down" in low:
        return {"reply":"Scrolling down.","action":{"name":"scroll","args":{"delta":-4}}}
    if "scroll up" in low:
        return {"reply":"Scrolling up.","action":{"name":"scroll","args":{"delta":4}}}
    if any(x in low for x in ["volume up","increase volume","turn volume up","louder"]):
        return {"reply":"Increasing volume.","action":{"name":"media_key","args":{"action":"volume_up"}}}
    if any(x in low for x in ["volume down","decrease volume","turn volume down","quieter"]):
        return {"reply":"Decreasing volume.","action":{"name":"media_key","args":{"action":"volume_down"}}}
    if any(x in low for x in ["mute sound","mute audio","mute volume","toggle mute","unmute"]):
        return {"reply":"Toggling mute.","action":{"name":"media_key","args":{"action":"mute"}}}
    if any(x in low for x in ["play pause","play or pause","pause media","resume media"]):
        return {"reply":"Toggling media playback.","action":{"name":"media_key","args":{"action":"play_pause"}}}
    if any(x in low for x in ["next track","next song","skip song"]):
        return {"reply":"Going to the next track.","action":{"name":"media_key","args":{"action":"next_track"}}}
    if any(x in low for x in ["previous track","previous song","last song"]):
        return {"reply":"Going to the previous track.","action":{"name":"media_key","args":{"action":"previous_track"}}}

    if low in {"open settings","open windows settings","windows settings","show settings"}:
        return {"reply":"Opening Windows Settings.","action":{"name":"open_start_app","args":{"query":"Settings"}}}
    if low in {"open file explorer","open explorer","file explorer"}:
        return {"reply":"Opening File Explorer.","action":{"name":"keyboard_action","args":{"action":"open_explorer"}}}

    m=re.match(r"^(?:please )?open (desktop|downloads|documents|pictures|videos|music)(?: folder)?[.!]?$",low)
    if m:
        return {"reply":f"Opening {m.group(1)}.","action":{"name":"open_known_location","args":{"name":m.group(1)}}}

    m=re.match(r"^(?:please )?(?:switch to|focus|bring up|bring back|show me|go to)\s+(.+?)[.!]?$",t,re.I)
    if m and len(m.group(1).strip())>1:
        query=m.group(1).strip()
        visible=visible_control_name(query)
        if visible:
            return {"reply":f"Opening {visible}.","action":{"name":"ui_invoke","args":{"label":visible}}}
        return {"reply":"Switching windows.","action":{"name":"focus_window","args":{"query":query}}}

    m=re.match(r"^(?:please )?(?:click|press|choose|select|activate|tap)\s+(.+?)[.!]?$",t,re.I)
    if m and len(m.group(1).strip())>0:
        requested=m.group(1).strip()
        resolved=visible_control_name(requested) or requested
        return {"reply":f"Activating {resolved}.","action":{"name":"ui_invoke","args":{"label":resolved}}}

    m=re.match(r"^(?:please )?(turn on|turn off|enable|disable)\s+(.+?)[.!]?$",t,re.I)
    if m and not any(x in low for x in ["computer"," pc","windows","shutdown","shut down"]):
        requested=m.group(2).strip()
        resolved=visible_control_name(requested) or requested
        desired="on" if m.group(1).lower() in {"turn on","enable"} else "off"
        return {"reply":f"Setting {resolved} {desired}.","action":{"name":"ui_set_toggle","args":{"label":resolved,"desired":desired}}}
    m=re.match(r"^(?:please )?toggle\s+(.+?)[.!]?$",t,re.I)
    if m:
        requested=m.group(1).strip()
        resolved=visible_control_name(requested) or requested
        return {"reply":f"Toggling {resolved}.","action":{"name":"ui_invoke","args":{"label":resolved}}}

    m=re.match(r"^(?:please )?(?:find|search for)\s+(.+?)\s+(?:on this page|in this window|here)[.!]?$",t,re.I)
    if m:
        return {"reply":"Opening Find and entering that text.","action":{"name":"desktop_sequence","args":{"steps":[
            {"name":"keyboard_action","args":{"action":"find"}},
            {"name":"type_text","args":{"text":m.group(1).strip(),"submit":False}}
        ]}}}

    if low in {"new tab","open new tab","new browser tab"}:
        return {"reply":"Opening a new tab.","action":{"name":"keyboard_action","args":{"action":"new_tab"}}}
    if low in {"close tab","close this tab","close current tab"}:
        return {"reply":"Closing the current tab.","action":{"name":"keyboard_action","args":{"action":"close_tab"}}}
    if low in {"reopen tab","reopen last tab","restore closed tab"}:
        return {"reply":"Reopening the last tab.","action":{"name":"keyboard_action","args":{"action":"reopen_tab"}}}
    if low in {"task view","show task view","show all windows"}:
        return {"reply":"Opening Task View.","action":{"name":"keyboard_action","args":{"action":"task_view"}}}
    shortcut_phrases={
        "start menu":"start_menu","open start menu":"start_menu","show start menu":"start_menu",
        "windows search":"search","open search":"search","search windows":"search",
        "quick settings":"quick_settings","open quick settings":"quick_settings",
        "notifications":"notifications","open notifications":"notifications","notification center":"notifications",
        "clipboard history":"clipboard_history","show clipboard":"clipboard_history","open clipboard history":"clipboard_history",
        "take screenshot":"screenshot","screenshot":"screenshot","snipping tool":"screenshot",
        "new desktop":"new_desktop","create new desktop":"new_desktop",
        "close desktop":"close_desktop","close current desktop":"close_desktop",
        "desktop left":"desktop_left","previous desktop":"desktop_left",
        "desktop right":"desktop_right","next desktop":"desktop_right",
        "task manager":"task_manager","open task manager":"task_manager",
    }
    if low in shortcut_phrases:
        return {"reply":"Doing that now.","action":{"name":"keyboard_action","args":{"action":shortcut_phrases[low]}}}

    if low in {"click","left click","click here"}:
        return {"reply":"Clicking at the current mouse position.","action":{"name":"mouse_voice","args":{"action":"click","dx":0,"dy":0}}}
    if low in {"double click","double click here"}:
        return {"reply":"Double clicking at the current mouse position.","action":{"name":"mouse_voice","args":{"action":"double","dx":0,"dy":0}}}
    if low in {"right click","right click here"}:
        return {"reply":"Right clicking at the current mouse position.","action":{"name":"mouse_voice","args":{"action":"right","dx":0,"dy":0}}}
    m=re.match(r"^(?:move|move mouse|move the mouse)\s+(left|right|up|down)(?:\s+(\d+))?(?:\s*(?:pixels|pixel|px))?[.!]?$",low)
    if m:
        amount=max(10,min(int(m.group(2) or 100),1000))
        dx=amount if m.group(1)=="right" else -amount if m.group(1)=="left" else 0
        dy=amount if m.group(1)=="down" else -amount if m.group(1)=="up" else 0
        return {"reply":"Moving the mouse.","action":{"name":"mouse_voice","args":{"action":"move","dx":dx,"dy":dy}}}
    m=re.match(r"^(?:drag|drag mouse|drag the mouse)\s+(left|right|up|down)(?:\s+(\d+))?(?:\s*(?:pixels|pixel|px))?[.!]?$",low)
    if m:
        amount=max(10,min(int(m.group(2) or 100),1000))
        dx=amount if m.group(1)=="right" else -amount if m.group(1)=="left" else 0
        dy=amount if m.group(1)=="down" else -amount if m.group(1)=="up" else 0
        return {"reply":"Dragging the mouse.","action":{"name":"mouse_voice","args":{"action":"drag","dx":dx,"dy":dy}}}

    m=re.match(r"^(?:please )?(?:open|visit|go to)(?:\s+(?:website|site))?\s+((?:https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/\S*)?)[.!]?$",t,re.I)
    if m:
        return {"reply":f"Opening {m.group(1)}.","action":{"name":"browser_open_url","args":{"url":m.group(1)}}}

    m=re.match(r"^(?:please )?(?:open|launch|start)\s+(.+?)[.!]?$",t,re.I)
    if m and len(m.group(1).strip())>1:
        query=m.group(1).strip()
        visible=visible_control_name(query)
        if visible:
            return {"reply":f"Opening {visible}.","action":{"name":"ui_invoke","args":{"label":visible}}}
        return {"reply":f"Opening {query}.","action":{"name":"open_start_app","args":{"query":query}}}

    if any(x in low for x in ["lock windows","lock pc","lock computer","lock the pc"]):
        return {"reply":"Locking Windows.","action":{"name":"lock_pc","args":{}}}
    if any(x in low for x in ["sleep pc","sleep computer","put pc to sleep","put computer to sleep"]):
        return {"reply":"Putting the PC to sleep requires confirmation.","action":{"name":"sleep_pc","args":{}}}
    m=re.search(r"(?:set|change).*?(?:sleep|sleep timeout).*?(\d+)\s*(?:minute|minutes|min)",low)
    if m:
        return {"reply":f"Changing AC sleep timeout to {m.group(1)} minutes.","action":{"name":"set_sleep_timeout","args":{"minutes":int(m.group(1))}}}
    if any(x in low for x in ["never sleep","keep pc awake","keep computer awake","disable sleep"]):
        return {"reply":"Setting AC sleep timeout to Never.","action":{"name":"set_sleep_timeout","args":{"minutes":0}}}
    if "restart" in low and any(x in low for x in ["pc","computer","windows"]):
        return {"reply":"PC restart requires confirmation.","action":{"name":"restart_pc","args":{}}}
    if ("shut down" in low or "shutdown" in low) and any(x in low for x in ["pc","computer","windows"]):
        return {"reply":"PC shutdown requires confirmation.","action":{"name":"shutdown_pc","args":{}}}
    return None
PLAN_SYSTEM = """You are the intent parser for a personal Windows PC voice controller.
Return ONLY one JSON object. Never output markdown.
Schema:
{"reply":"short spoken response","action":null}
or
{"reply":"short spoken response","action":{"name":"ACTION","args":{...}}}
Allowed ACTION values:
status, open_app, open_start_app, open_known_location, browser_search, browser_open_url, describe_ui, prompt_app, read_active, summarize_active, summarize_target,
send_active_summary, send_last_context, restart_rustdesk, minimize_all, minimize_open_this_pc, open_this_pc, open_drive,
window_action, named_window_action, app_window, focus_app, focus_window, list_windows, ui_invoke, ui_set_toggle, ui_set_text, mouse_voice, keyboard_action, type_text,
explorer_action, explorer_rename, explorer_new_folder, scroll, mouse_voice, media_key, desktop_sequence, voice_sequence,
lock_pc, sleep_pc, set_sleep_timeout, restart_pc, shutdown_pc.
open_app target: chrome, obsidian, antigravity, chatgpt, codex.
open_start_app args: {"query":"installed Start-menu app name"}.
open_known_location name: desktop, downloads, documents, pictures, videos, music.
prompt_app target: antigravity, chatgpt, codex.
summarize_target target: antigravity, chatgpt, codex.
send_active_summary target: antigravity, chatgpt, codex.
send_last_context target: antigravity, chatgpt, codex.
open_drive args: {"letter":"C"}.
window_action args action must be one of maximize, minimize, restore, close, left, right, next, previous, restore_all.
named_window_action args: {"query":"window title or process fragment","action":"maximize|minimize|restore|close|left|right"}.
app_window target: chrome, obsidian, antigravity, chatgpt, codex; action: maximize, minimize, restore, close.
focus_app target: chrome, obsidian, antigravity, chatgpt, codex.
focus_window args: {"query":"window title or process fragment"}.
ui_invoke args: {"label":"visible control text","control_type":""}. Use CURRENT DESKTOP OBSERVATION labels when possible.
ui_set_toggle args: {"label":"visible toggle label","desired":"on|off"}. Use this instead of blind toggling when the user says turn on/off or enable/disable.
ui_set_text args: {"label":"visible text field label or empty string","text":"text","submit":false}.
mouse_voice args: {"action":"move|click|double|right|drag","dx":integer,"dy":integer}. Use only when accessibility labels are unavailable.
keyboard_action args action must be one of enter, escape, tab, shift_tab, select_all, copy, cut, paste, undo, redo, save, delete, rename, refresh, new_folder, back, forward, find, new_tab, close_tab, reopen_tab, open_explorer, open_settings, lock, show_desktop, task_view, run_dialog, start_menu, search, quick_settings, notifications, clipboard_history, screenshot, new_desktop, close_desktop, desktop_left, desktop_right, task_manager, home, end, page_up, page_down, space.
mouse_voice args: {"action":"move|drag|click|double|right","dx":integer,"dy":integer}. Relative mouse motion is capped by the backend.
type_text args: {"text":"text to type into the currently focused field","submit":false}.
explorer_action args action must be one of open_selected, back, forward, up, copy, paste, next, previous, close.
explorer_rename args: {"name":"new name"}.
explorer_new_folder args: {"name":"folder name"}.
scroll args: {"delta": integer from -8 through 8}.
media_key action: volume_up, volume_down, mute, play_pause, next_track, previous_track, stop_media.
desktop_sequence args: {"steps":[{"name":"ACTION","args":{...}}, ...]}. Use this for explicit multi-step requests, maximum 8 steps.
voice_sequence args: {"commands":["natural language step 1","natural language step 2"]}. It is executed one step at a time with a fresh desktop observation.
set_sleep_timeout args: {"minutes": integer from 0 through 1440}. Zero means Never on AC power.
Never invent shell commands or arbitrary executable paths. Prefer accessibility controls and Start-menu apps.
Use the current desktop observation to interact with visible buttons, tabs, menu items, checkboxes, list items, and text fields.
For an explicit sequence such as "open Settings then click Bluetooth", return desktop_sequence.
For unclear requests, use action:null and ask one concise clarification.
If the user names Antigravity, Codex, or ChatGPT and asks to read, check, or summarize its latest response, use summarize_target for that named target.
If the user asks to read or summarize the currently visible window without naming a target, use summarize_active.
If the user explicitly asks to send or continue the current visible response in Codex, Antigravity, or ChatGPT, use send_active_summary.
"""

def heuristic_semantic_plan(user_text,desktop=None):
    text=str(user_text or "").strip()
    low=text.lower()
    cleaned=re.sub(r"\b(please|can you|could you|would you|i want you to|the|this|that|on|off|for|me)\b"," ",low)
    cleaned=re.sub(r"\b(click|press|choose|select|activate|tap|open|show|go to|turn|enable|disable|switch to|focus)\b"," ",cleaned)
    cleaned=re.sub(r"\s+"," ",cleaned).strip(" .?!")
    if cleaned:
        visible=visible_control_name(cleaned)
        if visible:
            return {"reply":f"Opening {visible}.","action":{"name":"ui_invoke","args":{"label":visible}}}
    m=re.match(r"^(?:open|launch|start)\s+(.+)$",text,re.I)
    if m:
        return {"reply":f"Opening {m.group(1).strip()}.","action":{"name":"open_start_app","args":{"query":m.group(1).strip()}}}
    m=re.match(r"^(?:switch to|focus|bring back|show me)\s+(.+)$",text,re.I)
    if m:
        return {"reply":"Switching windows.","action":{"name":"focus_window","args":{"query":m.group(1).strip()}}}
    return {"reply":"I need a little more detail about what you want me to control on the current screen.","action":None}

def llm_plan(user_text):
    context=recent_context(6)
    state={
        "last_summary": get_state("last_summary",""),
        "last_action": get_state("last_action",""),
        "last_target": get_state("last_target","")
    }
    try:
        raw_desktop=bridge.ui_snapshot(55)
        desktop={
            "window":raw_desktop.get("window",""),
            "process":raw_desktop.get("process",""),
            "controls":[
                {"type":e.get("type",""),"name":e.get("name",""),"toggle":e.get("toggle","")}
                for e in (raw_desktop.get("elements") or [])
                if e.get("name") and not e.get("offscreen")
            ][:45]
        }
    except Exception as e:
        desktop={"error":str(e)}
    try:
        windows=bridge.list_visible_windows(10)
    except Exception:
        windows=[]
    prompt=(
        "RECENT CONTEXT:\n"+json.dumps(context,ensure_ascii=False)+
        "\nAGENT STATE:\n"+json.dumps(state,ensure_ascii=False)+
        "\nVISIBLE WINDOWS:\n"+json.dumps(windows,ensure_ascii=False)+
        "\nCURRENT DESKTOP OBSERVATION:\n"+json.dumps(desktop,ensure_ascii=False)+
        "\nCURRENT USER COMMAND:\n"+user_text
    )
    raw=omni_chat(prompt,PLAN_SYSTEM,500)
    try:
        plan=parse_json_object(raw)
        if isinstance(plan,dict):
            return plan
    except Exception:
        pass
    retry_system=PLAN_SYSTEM+"\nCRITICAL: Return exactly one JSON object. Do not use markdown or explanatory prose."
    retry_prompt="DESKTOP:\n"+json.dumps(desktop,ensure_ascii=False)+"\nCOMMAND:\n"+str(user_text)
    try:
        raw2=omni_chat(retry_prompt,retry_system,350)
        plan=parse_json_object(raw2)
        if isinstance(plan,dict):
            return plan
    except Exception as e:
        log(f"PLAN RETRY ERROR {e}")
    return heuristic_semantic_plan(user_text,desktop)

def normalize_plan(plan):
    if not isinstance(plan,dict):
        return {"reply":"I did not understand that command.","action":None}
    reply=str(plan.get("reply") or "Done.")[:500]
    action=plan.get("action")
    if action is None:
        return {"reply":reply,"action":None}
    if not isinstance(action,dict):
        return {"reply":"I need a clearer command.","action":None}
    name=action.get("name")
    args=action.get("args") or {}
    allowed={"status","open_app","open_start_app","open_known_location","browser_search","browser_open_url","describe_ui","prompt_app","read_active",
             "summarize_active","summarize_target","send_active_summary","send_last_context",
             "restart_rustdesk","minimize_all","minimize_open_this_pc","open_this_pc","open_drive",
             "window_action","named_window_action","app_window","focus_app","focus_window","list_windows","ui_invoke","ui_set_toggle","ui_set_text","ui_control",
             "screen_describe","screen_click_text","vision_click","vision_target_click","vision_drag","wait_visual",
             "keyboard_action","type_text","explorer_action","explorer_rename","explorer_new_folder","scroll","mouse_voice","media_key",
             "native_list_folder","native_find_files","native_new_folder","native_copy_move","recycle_path",
             "native_processes","native_close_process","native_service","native_network_status","clipboard_read","clipboard_write",
             "desktop_sequence","voice_sequence","lock_pc","sleep_pc","set_sleep_timeout","restart_pc","shutdown_pc"}
    if name=="desktop_sequence":
        raw_steps=args.get("steps") or []
        if not isinstance(raw_steps,list) or not raw_steps:
            return {"reply":"I need a clear sequence of actions.","action":None}
        steps=[]
        for step in raw_steps[:8]:
            norm=normalize_plan({"reply":"Step.","action":step})
            if not norm.get("action") or norm["action"].get("name") in {"desktop_sequence","voice_sequence"}:
                return {"reply":"One of those steps is not safe or supported.","action":None}
            steps.append(norm["action"])
        return {"reply":reply,"action":{"name":"desktop_sequence","args":{"steps":steps}}}
    if name=="voice_sequence":
        raw_commands=args.get("commands") or []
        if not isinstance(raw_commands,list) or not raw_commands:
            return {"reply":"I need a clear sequence of voice steps.","action":None}
        commands=[]
        for item in raw_commands[:8]:
            s=str(item).strip()[:500]
            if not s:
                return {"reply":"One of those voice steps is empty.","action":None}
            commands.append(s)
        return {"reply":reply,"action":{"name":"voice_sequence","args":{"commands":commands}}}
    if name not in allowed:
        return {"reply":"That action is not allowed by the PC controller.","action":None}
    if name in {"open_app","focus_app","app_window"} and args.get("target") not in CONFIG["apps"]:
        return {"reply":"That app is not configured.","action":None}
    if name=="open_start_app":
        args["query"]=str(args.get("query","")).strip()[:120]
        if not args["query"]:
            return {"reply":"I need an app name.","action":None}
    if name=="browser_open_url":
        args["url"]=str(args.get("url","")).strip()[:500]
        if not args["url"]:
            return {"reply":"I need a website address.","action":None}
    if name=="describe_ui":
        args["brief"]=bool(args.get("brief",False))
    if name=="open_known_location":
        args["name"]=str(args.get("name","")).strip().lower()
        if args["name"] not in {"desktop","downloads","documents","pictures","videos","music"}:
            return {"reply":"That folder shortcut is not configured.","action":None}
    if name=="focus_window":
        args["query"]=str(args.get("query","")).strip()[:180]
        if not args["query"]:
            return {"reply":"I need a window name.","action":None}
    if name=="ui_invoke":
        args["label"]=str(args.get("label","")).strip()[:180]
        args["control_type"]=str(args.get("control_type","")).strip()[:40]
        if not args["label"]:
            return {"reply":"I need the visible control name.","action":None}
    if name=="ui_set_toggle":
        args["label"]=str(args.get("label","")).strip()[:180]
        args["desired"]=str(args.get("desired","")).strip().lower()
        if not args["label"] or args["desired"] not in {"on","off"}:
            return {"reply":"I need a visible toggle and an on or off state.","action":None}
    if name=="ui_set_text":
        args["label"]=str(args.get("label","")).strip()[:180]
        args["text"]=str(args.get("text",""))[:2000]
        args["submit"]=bool(args.get("submit",False))
        if not args["text"]:
            return {"reply":"I need text to enter.","action":None}
    if name=="ui_control":
        args["label"]=str(args.get("label","")).strip()[:180]
        args["operation"]=str(args.get("operation","invoke")).strip().lower()
        args["value"]=str(args.get("value",""))[:2000]
        args["desired"]=str(args.get("desired","")).strip().lower()
        args["control_type"]=str(args.get("control_type","")).strip()[:40]
        args["window_query"]=str(args.get("window_query","")).strip()[:180]
        if args["operation"] not in {"invoke","toggle","set_text","select","expand","collapse","focus","read"}:
            return {"reply":"That UI Automation operation is not allowed.","action":None}
        if args["operation"] not in {"set_text","select"} and not args["label"]:
            return {"reply":"I need the control name.","action":None}
        if args["operation"]=="toggle" and args["desired"] not in {"on","off",""}:
            return {"reply":"I need an on or off toggle state.","action":None}
        if args["operation"] in {"set_text","select"} and not args["value"]:
            return {"reply":"I need the text or option value.","action":None}
    if name in {"prompt_app","send_active_summary","send_last_context","summarize_target"} and args.get("target") not in {"antigravity","chatgpt","codex"}:
        return {"reply":"That target is not allowed for this action.","action":None}
    if name=="set_sleep_timeout":
        try:
            args["minutes"]=max(0,min(int(args.get("minutes",0)),1440))
        except:
            return {"reply":"I need a valid sleep timeout in minutes.","action":None}
    if name=="open_drive":
        letter=str(args.get("letter","")).strip().upper().replace(":","")
        if not re.fullmatch(r"[A-Z]",letter):
            return {"reply":"I need a valid drive letter.","action":None}
        args["letter"]=letter
    if name=="window_action" and args.get("action") not in {"maximize","minimize","restore","close","left","right","next","previous","restore_all"}:
        return {"reply":"That window action is not allowed.","action":None}
    if name=="named_window_action":
        args["query"]=str(args.get("query","")).strip()[:180]
        if not args["query"] or args.get("action") not in {"maximize","minimize","restore","close","left","right"}:
            return {"reply":"That named window action is not valid.","action":None}
    if name=="app_window" and args.get("action") not in {"maximize","minimize","restore","close"}:
        return {"reply":"That app window action is not allowed.","action":None}
    if name=="keyboard_action" and args.get("action") not in {"enter","escape","tab","shift_tab","select_all","copy","cut","paste","undo","redo","save","delete","rename","refresh","new_folder","back","forward","find","new_tab","close_tab","reopen_tab","open_explorer","open_settings","lock","show_desktop","task_view","run_dialog","start_menu","search","quick_settings","notifications","clipboard_history","screenshot","new_desktop","close_desktop","desktop_left","desktop_right","task_manager","home","end","page_up","page_down","space"}:
        return {"reply":"That keyboard action is not allowed.","action":None}
    if name=="mouse_voice":
        action_name=str(args.get("action","")).strip().lower()
        if action_name not in {"move","drag","click","double","right"}:
            return {"reply":"That mouse action is not allowed.","action":None}
        args["action"]=action_name
        try:
            args["dx"]=max(-2000,min(int(args.get("dx",0)),2000))
            args["dy"]=max(-2000,min(int(args.get("dy",0)),2000))
        except:
            return {"reply":"I need valid mouse movement values.","action":None}
    if name in {"explorer_rename","explorer_new_folder"}:
        args["name"]=str(args.get("name","")).strip()[:240]
        if not args["name"]:
            return {"reply":"I need a name for that item.","action":None}
    if name=="type_text":
        args["text"]=str(args.get("text",""))[:2000]
        args["submit"]=bool(args.get("submit",False))
        if not args["text"]:
            return {"reply":"I need text to type.","action":None}
    if name=="explorer_action" and args.get("action") not in {"open_selected","back","forward","up","copy","paste","next","previous","close"}:
        return {"reply":"That File Explorer action is not allowed.","action":None}
    if name=="scroll":
        try:
            args["delta"]=max(-8,min(int(args.get("delta",0)),8))
        except:
            return {"reply":"I need a valid scroll amount.","action":None}
    if name=="media_key" and args.get("action") not in {"volume_up","volume_down","mute","play_pause","next_track","previous_track","stop_media"}:
        return {"reply":"That media control is not allowed.","action":None}
    if name=="screen_describe":
        args["request"]=str(args.get("request","Describe what is visibly on the current screen."))[:600]
    if name=="screen_click_text":
        args["text"]=str(args.get("text","")).strip()[:180]
        args["kind"]=str(args.get("kind","click")).strip().lower()
        if not args["text"] or args["kind"] not in {"click","double","right"}:
            return {"reply":"I need visible screen text and a valid click type.","action":None}
    if name=="vision_click":
        try:
            vx=float(args.get("x",0.5)); vy=float(args.get("y",0.5))
            # Vision models sometimes return a 0..1000 coordinate grid even
            # when asked for 0..1 normalized coordinates. Normalize that safely.
            if vx>1.0 or vy>1.0:
                if 0.0<=vx<=1000.0 and 0.0<=vy<=1000.0:
                    vx/=1000.0; vy/=1000.0
            args["x"]=max(0.0,min(vx,1.0))
            args["y"]=max(0.0,min(vy,1.0))
        except:
            return {"reply":"I need valid screen coordinates.","action":None}
        args["kind"]=str(args.get("kind","click")).strip().lower()
        args["label"]=str(args.get("label","visual target")).strip()[:180]
        if args["kind"] not in {"click","double","right"}:
            return {"reply":"That visual click type is not allowed.","action":None}
    if name=="vision_target_click":
        args["target"]=str(args.get("target","")).strip()[:300]
        args["kind"]=str(args.get("kind","click")).strip().lower()
        args["scope"]=str(args.get("scope","active_window")).strip().lower()
        if not args["target"] or args["kind"] not in {"click","double","right"}:
            return {"reply":"I need a visible target and valid click type.","action":None}
        if args["scope"] not in {"active_window","desktop"}:
            args["scope"]="active_window"
    if name=="vision_drag":
        try:
            vals=[]
            for key in ("x1","y1","x2","y2"):
                v=float(args.get(key,0))
                if v>1.0 and 0<=v<=1000.0: v/=1000.0
                vals.append(max(0.0,min(v,1.0)))
            args["x1"],args["y1"],args["x2"],args["y2"]=vals
            args["duration"]=max(0.15,min(float(args.get("duration",0.55)),2.5))
        except:
            return {"reply":"I need valid drag coordinates.","action":None}
        args["label"]=str(args.get("label","visual drag")).strip()[:220]
    if name=="wait_visual":
        args["description"]=str(args.get("description","")).strip()[:400]
        try: args["timeout"]=max(1.0,min(float(args.get("timeout",12)),30.0))
        except: args["timeout"]=12.0
        if not args["description"]:
            return {"reply":"I need the visual state to wait for.","action":None}
    if name in {"native_list_folder","native_new_folder","recycle_path"}:
        args["path"]=str(args.get("path","")).strip()[:1200]
        if not args["path"]:
            return {"reply":"I need a file or folder path.","action":None}
    if name=="native_find_files":
        args["root"]=str(args.get("root","")).strip()[:1200]
        args["query"]=str(args.get("query","")).strip()[:240]
        if not args["root"] or not args["query"]:
            return {"reply":"I need a search folder and file name.","action":None}
    if name=="native_copy_move":
        args["source"]=str(args.get("source","")).strip()[:1200]
        args["destination"]=str(args.get("destination","")).strip()[:1200]
        args["move"]=bool(args.get("move",False))
        args["overwrite"]=bool(args.get("overwrite",False))
        if not args["source"] or not args["destination"]:
            return {"reply":"I need both source and destination paths.","action":None}
    if name=="native_close_process":
        args["query"]=str(args.get("query","")).strip()[:180]
        args["force"]=bool(args.get("force",False))
        if not args["query"]:
            return {"reply":"I need a process or window name.","action":None}
    if name=="native_service":
        args["name"]=str(args.get("name","")).strip()[:180]
        args["action"]=str(args.get("action","status")).strip().lower()
        if not args["name"] or args["action"] not in {"status","start","stop","restart"}:
            return {"reply":"I need a valid service name and action.","action":None}
    if name=="clipboard_write":
        args["text"]=str(args.get("text",""))[:12000]
        if not args["text"]:
            return {"reply":"I need clipboard text.","action":None}
    return {"reply":reply,"action":{"name":name,"args":args}}
def should_use_universal(action):
    if not action:
        return False
    name=str(action.get("name") or "")
    # Interactive desktop/file/app operations should be observed, acted, and
    # verified through the universal loop instead of being treated as fire-and-forget.
    return name in {
        "open_app","open_start_app","open_known_location","browser_search","browser_open_url",
        "minimize_all","minimize_open_this_pc","open_this_pc","open_drive",
        "window_action","named_window_action","app_window","focus_app","focus_window",
        "ui_invoke","ui_set_toggle","ui_set_text","ui_control","keyboard_action","type_text",
        "explorer_action","explorer_rename","explorer_new_folder","scroll","mouse_voice",
        "screen_click_text","vision_click","vision_target_click","vision_drag","wait_visual",
        "native_list_folder","native_find_files","native_new_folder","native_copy_move","recycle_path",
        "native_close_process","native_service","clipboard_write"
    }

def risk_for(action):
    if not action:
        return "none"
    name=action.get("name")
    args=action.get("args") or {}
    if name=="desktop_sequence":
        for step in args.get("steps") or []:
            if risk_for(step)=="confirm":
                return "confirm"
        return "safe"
    if name=="voice_sequence":
        joined=" ".join(str(x).lower() for x in (args.get("commands") or []))
        risky=["delete","remove","uninstall","format","erase","factory reset","reset this pc",
               "shutdown","shut down","restart pc","restart computer","sleep pc","sleep computer",
               "close ","paste","send ","submit","buy","purchase","pay","sign out",
               "powershell","command prompt","terminal","registry","regedit","disk management","services"]
        return "confirm" if any(x in joined for x in risky) else "safe"
    if name in {"restart_pc","shutdown_pc","sleep_pc","prompt_app","send_active_summary","send_last_context"}:
        return "confirm"
    if name in {"window_action","named_window_action","app_window"} and args.get("action")=="close":
        return "confirm"
    if name=="keyboard_action" and args.get("action")=="delete":
        return "confirm"
    if name=="explorer_action" and args.get("action")=="paste":
        return "confirm"
    if name in {"type_text","ui_set_text"} and bool(args.get("submit",False)):
        return "confirm"
    if name in {"ui_invoke","ui_control"}:
        label=str(args.get("label","")).strip().lower()
        value=str(args.get("value","")).strip().lower()
        combined=label+" "+value
        if label in {"yes","confirm","confirm action"} or any(x in combined for x in ["delete","remove","uninstall","format","erase","reset","factory reset","purchase","buy","pay","send money","sign out"]):
            return "confirm"
    if name in {"screen_click_text","vision_click","vision_target_click","vision_drag"}:
        label=str(args.get("text") or args.get("target") or args.get("label") or "").strip().lower()
        if any(x in label for x in ["delete","remove","uninstall","format","erase","reset","factory reset","purchase","buy","pay","send","submit","sign out","approve","confirm"]):
            return "confirm"
    if name=="native_copy_move" and (bool(args.get("move")) or bool(args.get("overwrite"))):
        return "confirm"
    if name=="recycle_path":
        return "confirm"
    if name=="native_close_process":
        return "confirm"
    if name=="native_service" and str(args.get("action","status")).lower()!="status":
        return "confirm"
    if name=="open_start_app":
        q=str(args.get("query","")).lower()
        if any(x in q for x in ["powershell","command prompt","cmd","terminal","registry","regedit","disk management","services"]):
            return "confirm"
    return "safe"

def execute(action):
    name=action["name"]; args=action.get("args") or {}
    if name=="status":
        s=system_status()
        spoken=(f"{s.get('Computer','PC')} CPU {s.get('CPU','?')} percent, "
                f"RAM {s.get('RAMUsedGB','?')} of {s.get('RAMTotalGB','?')} gigabytes. "
                f"Tailscale {s['tailscale']['state']}, RustDesk {s['rustdesk']['state']}, "
                f"OpenSSH {s['openssh']['state']}.")
        return spoken, s
    if name=="screen_describe":
        result=describe_visual_screen(str(args.get("request","Describe what is visibly on the current screen.")))
        return result.get("spoken") or result.get("summary") or "I inspected the screen.", result
    if name=="screen_click_text":
        before=bridge.screen_fingerprint()
        data=bridge.click_screen_text(str(args.get("text","")),str(args.get("kind","click")))
        time.sleep(0.45)
        after=bridge.screen_fingerprint()
        data["screen_changed"]=before.get("sha256")!=after.get("sha256")
        return f"Clicked {data.get('matched_text') or args.get('text')}.", data
    if name=="vision_click":
        before=bridge.screen_fingerprint()
        data=bridge.pointer_action(float(args.get("x",0.5)),float(args.get("y",0.5)),str(args.get("kind","click")))
        time.sleep(0.45)
        after=bridge.screen_fingerprint()
        data["label"]=str(args.get("label","visual target"))
        data["screen_changed"]=before.get("sha256")!=after.get("sha256")
        return f"Activated {data['label']}.", data
    if name=="vision_target_click":
        data=vision_target_click(str(args.get("target","")),str(args.get("kind","click")),str(args.get("scope","active_window")))
        return f"Visually activated {data.get('label') or args.get('target')}.", data
    if name=="vision_drag":
        before=bridge.screen_fingerprint()
        data=bridge.pointer_drag(float(args.get("x1",0)),float(args.get("y1",0)),float(args.get("x2",0)),float(args.get("y2",0)),float(args.get("duration",0.55)))
        time.sleep(0.5)
        after=bridge.screen_fingerprint()
        data["label"]=str(args.get("label","visual drag"))
        data["screen_changed"]=before.get("sha256")!=after.get("sha256")
        return f"Dragged {data['label']}.", data
    if name=="wait_visual":
        data=wait_for_visual_state(str(args.get("description","")),float(args.get("timeout",12)))
        if not data.get("found"):
            raise RuntimeError(f"Timed out waiting for {args.get('description','the visual state')}.")
        return f"Detected {args.get('description','the requested visual state')}.", data
    if name=="native_list_folder":
        data=bridge.native_list_folder(str(args.get("path","")))
        return f"Listed {len(data.get('items',[]))} items in {data.get('path','the folder')}.", data
    if name=="native_find_files":
        data=bridge.native_find_files(str(args.get("root","")),str(args.get("query","")))
        return f"Found {len(data.get('items',[]))} matching items.", data
    if name=="native_new_folder":
        data=bridge.native_new_folder(str(args.get("path","")))
        return f"Created folder {data.get('path','')}.", data
    if name=="native_copy_move":
        data=bridge.native_copy_move(str(args.get("source","")),str(args.get("destination","")),bool(args.get("move",False)),bool(args.get("overwrite",False)))
        return ("Moved" if args.get("move") else "Copied")+" the item successfully.", data
    if name=="recycle_path":
        data=bridge.recycle_path(str(args.get("path","")))
        return "Moved the item to the Recycle Bin.", data
    if name=="native_processes":
        data=bridge.native_processes()
        return f"Found {len(data)} running processes.", {"processes":data}
    if name=="native_close_process":
        data=bridge.native_close_process(str(args.get("query","")),bool(args.get("force",False)))
        return "Close request sent to the process.", data
    if name=="native_service":
        data=bridge.native_service_action(str(args.get("name","")),str(args.get("action","status")))
        return f"Service {args.get('name','')} {args.get('action','status')} completed.", data
    if name=="native_network_status":
        data=bridge.native_network_status()
        return "Collected the current network status.", data
    if name=="clipboard_read":
        value=bridge.clipboard_get_text()
        return bridge.clean_for_speech(value[:1800] if value else "The text clipboard is empty."), {"text":value}
    if name=="clipboard_write":
        data=bridge.clipboard_set_text(str(args.get("text","")))
        return "Copied that text to the clipboard.", data
    if name=="desktop_sequence":
        results=[]
        last_spoken="Done."
        steps=args.get("steps") or []
        for i,step in enumerate(steps,1):
            spoken,data=execute(step)
            results.append({"step":i,"action":step,"spoken":spoken,"data":data})
            last_spoken=spoken
            if step.get("name") in {"open_app","open_start_app","open_known_location","open_this_pc","open_drive"}:
                time.sleep(1.6)
            else:
                time.sleep(0.55)
        return f"Completed {len(results)} actions. {last_spoken}", {"steps":results}
    if name=="voice_sequence":
        commands=args.get("commands") or []
        confirmed=bool(args.get("_confirmed",False))
        results=[]
        last_spoken="Done."
        focus_hint=""
        for i,raw_command in enumerate(commands,1):
            if i>1 and focus_hint:
                try:
                    bridge.focus_window(focus_hint)
                    time.sleep(0.25)
                except Exception:
                    pass
            sub=local_rule(str(raw_command),False)
            if sub is None:
                sub=llm_plan(str(raw_command))
            sub=normalize_plan(sub)
            sub_action=sub.get("action")
            if not sub_action:
                raise RuntimeError(f"Step {i} needs clarification: {sub.get('reply','I did not understand it.')}")
            if risk_for(sub_action)=="confirm" and not confirmed:
                raise RuntimeError(f"Step {i} needs confirmation before I can continue.")
            sub_name=sub_action.get("name")
            sub_args=sub_action.get("args") or {}
            if sub_name=="window_action" and focus_hint and sub_args.get("action") in {"maximize","minimize","restore","close","left","right"}:
                sub_action={"name":"named_window_action","args":{"query":focus_hint,"action":sub_args.get("action")}}
                spoken,data=execute(sub_action)
                sub_name="named_window_action"
                sub_args=sub_action["args"]
            elif sub_name=="ui_invoke" and focus_hint:
                msg=bridge.ui_invoke_named_window(focus_hint,str(sub_args.get("label","")),str(sub_args.get("control_type","")))
                spoken,data=msg,{"message":msg,"window":focus_hint}
            elif sub_name=="keyboard_action" and focus_hint and sub_args.get("action") in {"back","forward"}:
                label="Back" if sub_args.get("action")=="back" else "Forward"
                msg=bridge.ui_invoke_named_window(focus_hint,label)
                spoken,data=msg,{"message":msg,"window":focus_hint}
            else:
                spoken,data=execute(sub_action)
            results.append({"step":i,"command":raw_command,"action":sub_action,"spoken":spoken,"data":data})
            last_spoken=spoken
            if sub_name=="open_start_app":
                focus_hint=str(sub_args.get("query",""))
            elif sub_name in {"open_app","focus_app","app_window"}:
                focus_hint=str(sub_args.get("target",""))
            elif sub_name in {"focus_window","named_window_action"}:
                focus_hint=str(sub_args.get("query",""))
            elif sub_name=="keyboard_action" and sub_args.get("action")=="open_settings":
                focus_hint="Settings"
            elif sub_name=="keyboard_action" and sub_args.get("action")=="open_explorer":
                focus_hint="File Explorer"
            elif sub_name in {"open_known_location","open_this_pc","open_drive","explorer_action","explorer_rename","explorer_new_folder"}:
                focus_hint="File Explorer"
            if sub_name in {"open_app","open_start_app","open_known_location","open_this_pc","open_drive","focus_window","focus_app","ui_invoke"} or (sub_name=="keyboard_action" and sub_args.get("action") in {"open_settings","open_explorer"}):
                time.sleep(1.0 if sub_name=="ui_invoke" else 1.6)
            else:
                time.sleep(0.55)
        return f"Completed {len(results)} voice steps. {last_spoken}", {"steps":results}
    if name=="open_app":
        msg=launch_app(str(args.get("target","")))
        return msg, {"message":msg}
    if name=="open_start_app":
        msg=bridge.open_start_app(str(args.get("query","")))
        return msg, {"message":msg}
    if name=="open_known_location":
        msg=bridge.open_known_location(str(args.get("name","")))
        return msg, {"message":msg}
    if name=="browser_open_url":
        msg=browser_open_url(str(args.get("url","")))
        return msg, {"message":msg}
    if name=="describe_ui":
        snap=bridge.ui_snapshot(120)
        window=(snap.get("window") or "the current window").strip()
        process=(snap.get("process") or "").strip()
        if bool(args.get("brief",False)):
            spoken=f"You are in {window}."
            if process and process.lower() not in window.lower():
                spoken+=f" The application process is {process}."
            return bridge.clean_for_speech(spoken), snap
        names=[]
        ignored_prefix=("minimize ","maximize ","restore ","close ")
        useful={"Button","TabItem","ListItem","Hyperlink","CheckBox","RadioButton","MenuItem","TreeItem","Edit","ComboBox"}
        for e in snap.get("elements") or []:
            if e.get("offscreen") or not e.get("enabled",True) or e.get("type") not in useful:
                continue
            label=str(e.get("name") or "").strip()
            if not label or label.lower().startswith(ignored_prefix) or label in names:
                continue
            names.append(label)
            if len(names)>=16:
                break
        if names:
            spoken=f"You are in {window}. Available controls include "+", ".join(names)+"."
        else:
            spoken=f"You are in {window}. I could not find clearly labeled controls on this screen."
        return bridge.clean_for_speech(spoken), snap
    if name=="focus_window":
        query=str(args.get("query",""))
        try:
            msg=bridge.focus_window(query)
        except Exception:
            msg=bridge.open_start_app(query)
        return msg, {"message":msg}
    if name=="ui_invoke":
        msg=bridge.ui_invoke(str(args.get("label","")),str(args.get("control_type","")))
        return msg, {"message":msg}
    if name=="ui_set_toggle":
        msg=bridge.ui_set_toggle(str(args.get("label","")),str(args.get("desired","")))
        return msg, {"message":msg}
    if name=="ui_set_text":
        msg=bridge.ui_set_text(str(args.get("label","")),str(args.get("text","")),bool(args.get("submit",False)))
        return msg, {"message":msg}
    if name=="ui_control":
        msg,data=bridge.ui_control(
            label=str(args.get("label","")),
            operation=str(args.get("operation","invoke")),
            value=str(args.get("value","")),
            desired=str(args.get("desired","")),
            control_type=str(args.get("control_type","")),
            window_query=str(args.get("window_query","")),
        )
        return msg, data
    if name=="focus_app":
        target=str(args.get("target",""))
        meta=CONFIG["apps"].get(target) or {}
        try:
            msg=bridge.focus_named_process(str(meta.get("process") or ""),target)
        except Exception:
            msg=bridge.launch_app(target,meta)
        return msg, {"message":msg,"target":target}
    if name=="list_windows":
        rows=bridge.list_visible_windows()
        if not rows:
            return "I could not find any visible windows.", {"windows":[]}
        names=[]
        for row in rows[:8]:
            title=(row.get("title") or "").strip()
            proc=(row.get("process") or "").strip()
            names.append(title if title else proc)
        spoken="Open windows include " + ", ".join(names) + "."
        return bridge.clean_for_speech(spoken), {"windows":rows}
    if name=="browser_search":
        msg=browser_search(str(args.get("query",""))[:500])
        return msg, {"message":msg}
    if name=="browser_open_url":
        msg=browser_open_url(str(args.get("url","")))
        return msg, {"message":msg}
    if name=="describe_ui":
        msg=describe_ui(bool(args.get("brief",False)))
        return msg, {"message":msg}
    if name=="prompt_app":
        prompt=str(args.get("prompt",""))[:4000]
        msg=prompt_app(str(args.get("target","")),prompt)
        return msg, {"message":msg}
    if name=="summarize_target":
        target=str(args.get("target",""))
        summary=summarize_target_response(target)
        return summary, {"summary":summary,"target":target}
    if name=="send_active_summary":
        msg=send_active_summary_to_app(str(args.get("target","")))
        return msg, {"message":msg}
    if name=="send_last_context":
        msg=send_last_context_to_app(str(args.get("target","")))
        return msg, {"message":msg}
    if name=="read_active":
        text=active_window_text()
        return bridge.clean_for_speech(text[:1800] if text else "I could not read the active window."), {"text":text}
    if name=="summarize_active":
        text=active_window_text()
        summary=summarize_text(text)
        return summary, {"summary":summary,"captured_chars":len(text)}
    if name=="minimize_all":
        msg=bridge.minimize_all()
        return msg, {"message":msg}
    if name=="minimize_open_this_pc":
        bridge.minimize_all()
        time.sleep(0.35)
        msg=bridge.open_this_pc()
        return "Minimized all windows and opened This PC.", {"message":msg}
    if name=="open_this_pc":
        msg=bridge.open_this_pc()
        return msg, {"message":msg}
    if name=="open_drive":
        msg=bridge.open_drive(args.get("letter",""))
        return msg, {"message":msg}
    if name=="window_action":
        msg=bridge.window_action(str(args.get("action","")))
        return msg, {"message":msg}
    if name=="named_window_action":
        msg=bridge.named_window_action(str(args.get("query","")),str(args.get("action","")))
        return msg, {"message":msg}
    if name=="app_window":
        target=str(args.get("target",""))
        meta=CONFIG["apps"].get(target) or {}
        msg=bridge.app_window(meta,target,str(args.get("action","")))
        return msg, {"message":msg,"target":target}
    if name=="keyboard_action":
        msg=bridge.keyboard_action(str(args.get("action","")))
        return msg, {"message":msg}
    if name=="type_text":
        msg=bridge.type_text(str(args.get("text","")),bool(args.get("submit",False)))
        return msg, {"message":msg}
    if name=="explorer_rename":
        msg=bridge.explorer_rename(str(args.get("name","")))
        return "Renamed the selected item.", {"message":msg}
    if name=="explorer_new_folder":
        msg=bridge.explorer_new_folder(str(args.get("name","")))
        return f"Created folder {args.get('name','')}.", {"message":msg}
    if name=="explorer_action":
        action_name=str(args.get("action",""))
        if action_name=="close":
            msg=bridge.explorer_window_action("close")
        else:
            msg=bridge.explorer_keys(action_name)
        return msg, {"message":msg}
    if name=="scroll":
        data=bridge.scroll_screen(int(args.get("delta",0)))
        return "Scrolled the screen.", data
    if name=="mouse_voice":
        msg=bridge.mouse_voice(str(args.get("action","")),int(args.get("dx",0)),int(args.get("dy",0)))
        return msg, {"message":msg}
    if name=="media_key":
        msg=bridge.media_key(str(args.get("action","")))
        return msg, {"message":msg}
    if name=="restart_rustdesk":
        rc,out,err=run_ps("Restart-Service -Name 'RustDesk' -Force -ErrorAction Stop",45)
        if rc: raise RuntimeError(err or out or "RustDesk restart failed")
        return "RustDesk restarted.", {"service":"RustDesk","state":service_state("RustDesk")}
    if name=="lock_pc":
        msg=lock_windows()
        return msg, {"locked":True}
    if name=="sleep_pc":
        msg=sleep_windows()
        return msg, {"sleep_requested":True}
    if name=="set_sleep_timeout":
        msg=set_ac_sleep_minutes(args.get("minutes",0))
        return msg, {"sleep":get_sleep_settings()}
    if name=="restart_pc":
        subprocess.run(["shutdown.exe","/r","/t","30","/c","Voice PC Agent requested restart"],creationflags=subprocess.CREATE_NO_WINDOW)
        return "Restart scheduled in 30 seconds.", {"scheduled":True,"seconds":30}
    if name=="shutdown_pc":
        subprocess.run(["shutdown.exe","/s","/t","30","/c","Voice PC Agent requested shutdown"],creationflags=subprocess.CREATE_NO_WINDOW)
        return "Shutdown scheduled in 30 seconds.", {"scheduled":True,"seconds":30}
    raise ValueError("Unsupported action")

def remember_result(action, spoken, data):
    try:
        set_state("last_action",action.get("name",""))
        args=action.get("args") or {}
        if args.get("target"):
            set_state("last_target",str(args["target"]))
        if action.get("name") in {"summarize_active","summarize_target"} and isinstance(data,dict) and data.get("summary"):
            set_state("last_summary",str(data["summary"]))
        elif action.get("name")=="read_active" and isinstance(data,dict) and data.get("text"):
            set_state("last_screen_text",str(data["text"])[:14000])
        set_state("last_spoken",spoken)
    except Exception as e:
        log(f"STATE SAVE ERROR {e}")

class CommandRequest(BaseModel):
    text: str

class ConfirmRequest(BaseModel):
    confirmation_id: str

class SpeechRequest(BaseModel):
    text: str

class PointerRequest(BaseModel):
    x: float
    y: float
    kind: str = "click"

class ScrollRequest(BaseModel):
    delta: int


# ---- Universal screen-aware autonomous control ----

UNIVERSAL_SYSTEM = r"""
You are the SHAL Windows desktop controller. You can SEE the current screenshot and also receive
Windows UI Automation controls, OCR extracted directly from screen pixels, open windows, and prior action results.
Plan exactly ONE next action toward the user's overall goal, then the controller will execute it and give you
a fresh screenshot/state before you plan again.

Return ONLY one JSON object:
{"status":"continue|done|blocked","reply":"short natural sentence","expected":"what should be visibly/systemically true after the action","action":{"name":"ACTION","args":{...}}}
For done or blocked, action must be null.

Rules:
- Never claim success unless the CURRENT screenshot/state proves the goal is complete.
- Prefer reliable methods in this order: native typed action, UI Automation by visible label, OCR text click, keyboard shortcut, vision target locator, raw vision coordinate click.
- When accessibility data is missing but the target is visibly identifiable, prefer vision_target_click with a concrete visual description. It performs a second screenshot-based target-localization pass and clicks the detected center.
- Use raw vision_click only when you can identify a clear coordinate directly from the screenshot.
- Coordinates for vision_click are normalized 0.0 to 1.0 over the entire screenshot.
- Do not invent shell commands, PowerShell, CMD, executable paths, passwords, keys, or credentials.
- Do not bypass login, UAC Secure Desktop, authentication, CAPTCHA, or security boundaries.
- If a risky action is necessary, select the appropriate typed action; the controller will request confirmation.
- If the goal is ambiguous or impossible from the current state, return blocked with the exact blocker.
- Do not use a precomputed multi-step sequence. One action only.

Available actions:
status {}
open_app {"target":"chrome|obsidian|antigravity|chatgpt|codex"}
open_start_app {"query":"installed app name"}
open_known_location {"name":"desktop|downloads|documents|pictures|videos|music"}
browser_search {"query":"search text"}
browser_open_url {"url":"https://..."}
focus_window {"query":"title or process fragment"}
list_windows {}
window_action {"action":"maximize|minimize|restore|close|left|right|next|previous|restore_all"}
named_window_action {"query":"window","action":"maximize|minimize|restore|close|left|right"}
ui_invoke {"label":"visible accessible control","control_type":""}
ui_set_toggle {"label":"toggle label","desired":"on|off"}
ui_set_text {"label":"text field label or empty","text":"text","submit":false}
ui_control {"label":"control label","operation":"invoke|toggle|set_text|select|expand|collapse|focus|read","value":"","desired":"on|off","control_type":"","window_query":""}
screen_describe {"request":"what to inspect visually"}
screen_click_text {"text":"text visibly rendered on screen","kind":"click|double|right"}
vision_click {"x":0.5,"y":0.5,"kind":"click|double|right","label":"what is being clicked"}
vision_target_click {"target":"visual description or label","kind":"click|double|right","scope":"active_window|desktop"}
vision_drag {"x1":0.2,"y1":0.3,"x2":0.7,"y2":0.6,"duration":0.55,"label":"what is being dragged"}
wait_visual {"description":"visible state to wait for","timeout":12}
keyboard_action {"action":"enter|escape|tab|shift_tab|select_all|copy|cut|paste|undo|redo|save|delete|rename|refresh|new_folder|back|forward|find|new_tab|close_tab|reopen_tab|open_explorer|open_settings|lock|show_desktop|task_view|run_dialog|start_menu|search|quick_settings|notifications|clipboard_history|screenshot|new_desktop|close_desktop|desktop_left|desktop_right|task_manager|home|end|page_up|page_down|space"}
type_text {"text":"text","submit":false}
scroll {"delta":-8..8}
media_key {"action":"volume_up|volume_down|mute|play_pause|next_track|previous_track|stop_media"}
native_list_folder {"path":"absolute or expanded Windows path"}
native_find_files {"root":"folder","query":"name fragment"}
native_new_folder {"path":"full folder path"}
native_copy_move {"source":"path","destination":"path","move":false,"overwrite":false}
recycle_path {"path":"path"}
native_processes {}
native_close_process {"query":"process/window","force":false}
native_service {"name":"service name","action":"status|start|stop|restart"}
native_network_status {}
clipboard_read {}
clipboard_write {"text":"text"}
open_this_pc {}
open_drive {"letter":"C"}
explorer_action {"action":"open_selected|back|forward|up|copy|paste|next|previous|close"}
explorer_rename {"name":"new name"}
explorer_new_folder {"name":"folder name"}
lock_pc {}
sleep_pc {}
set_sleep_timeout {"minutes":30}
restart_pc {}
shutdown_pc {}
"""

def omni_vision_chat(prompt, system, max_tokens=1800, image_bytes=None, timeout=75):
    if not ensure_omniroute_server():
        raise RuntimeError("OmniRoute server is unavailable.")
    if image_bytes is None:
        image_bytes,*_=bridge.screen_jpeg(1100,52)
    data_url="data:image/jpeg;base64,"+base64.b64encode(image_bytes).decode("ascii")
    payload={
        "model":VISION_MODEL,
        "messages":[
            {"role":"system","content":system},
            {"role":"user","content":[
                {"type":"text","text":str(prompt)},
                {"type":"image_url","image_url":{"url":data_url}}
            ]}
        ],
        "max_tokens":int(max_tokens),
        "temperature":0.05,
        "reasoning_effort":"low"
    }
    r=requests.post("http://127.0.0.1:20128/v1/chat/completions",json=payload,timeout=max(10,int(timeout)))
    if r.status_code>=400:
        raise RuntimeError(f"Vision HTTP {r.status_code}: {r.text[:500]}")
    data=r.json()
    msg=((data.get("choices") or [{}])[0].get("message") or {})
    content=str(msg.get("content") or "").strip()
    if not content:
        content=str(msg.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError("Vision model returned no usable content.")
    return content

VISION_TARGET_SYSTEM = r"""
You are a precise Windows screenshot target locator. Locate ONE requested visible target using the actual screenshot pixels.
UI Automation and OCR context are supporting evidence only. The target may be custom-drawn and absent from accessibility data.
Return ONLY JSON:
{"found":true,"label":"what you found","x":0.5,"y":0.5,"width":0.1,"height":0.05,"confidence":0.95,"evidence":"brief visual evidence"}
Coordinates and size are normalized 0.0..1.0 over the supplied screenshot. x/y are the target center.
If the target is not clearly visible, return {"found":false,"label":"","x":0,"y":0,"width":0,"height":0,"confidence":0,"evidence":"why not found"}.
Never guess a target that is obscured, off-screen, ambiguous, or only inferred from hidden state.
"""

def _uia_correlation_for_point(screen_x,screen_y,state=None):
    """Return the smallest visible UIA element containing an absolute desktop point."""
    state=state or compact_desktop_state(False)
    hits=[]
    for e in (state.get("controls") or []):
        r=e.get("rect") or [0,0,0,0]
        try:
            rx,ry,rw,rh=[float(v) for v in r[:4]]
        except Exception:
            continue
        if rw<=0 or rh<=0:
            continue
        if rx<=screen_x<=rx+rw and ry<=screen_y<=ry+rh:
            hits.append((rw*rh,e))
    if not hits:
        return None
    return min(hits,key=lambda x:x[0])[1]

def vision_locate_target(target,scope="active_window",min_confidence=0.62):
    """Locate a target from screenshot pixels and correlate it with UIA when possible."""
    target=str(target or "").strip()
    if not target:
        raise ValueError("A visual target description is required.")
    raw,meta=bridge.screen_capture_jpeg(scope,1350,60)
    state=compact_desktop_state(True)
    prompt=(
        "TARGET TO LOCATE:\n"+target+
        "\n\nCAPTURE METADATA:\n"+json.dumps(meta,ensure_ascii=False)+
        "\n\nSUPPORTING UI/OCR STATE:\n"+json.dumps(state,ensure_ascii=False)[:18000]
    )
    answer=omni_vision_chat(prompt,VISION_TARGET_SYSTEM,650,raw,timeout=50)
    obj=parse_json_object(answer)
    found=bool(obj.get("found",False))
    try:
        conf=float(obj.get("confidence",0) or 0)
        x=float(obj.get("x",0)); y=float(obj.get("y",0))
        w=float(obj.get("width",0) or 0); h=float(obj.get("height",0) or 0)
    except Exception:
        found=False; conf=0; x=y=w=h=0
    if x>1 or y>1:
        if 0<=x<=1000 and 0<=y<=1000:
            x/=1000.0; y/=1000.0
        else:
            found=False
    x=max(0.0,min(x,1.0)); y=max(0.0,min(y,1.0))
    w=max(0.0,min(w,1.0)); h=max(0.0,min(h,1.0))
    if not found or conf<float(min_confidence):
        return {
            "found":False,"target":target,"confidence":round(conf,3),
            "evidence":str(obj.get("evidence") or "Target was not confidently visible.")[:1200],
            "capture":meta
        }
    cap_native_w=max(float(meta.get("native_width") or 1),1)
    cap_native_h=max(float(meta.get("native_height") or 1),1)
    abs_x=float(meta.get("origin_x") or 0)+x*cap_native_w
    abs_y=float(meta.get("origin_y") or 0)+y*cap_native_h
    vx=float(meta.get("virtual_left",0)); vy=float(meta.get("virtual_top",0))
    vw=max(float(meta.get("virtual_width") or cap_native_w),1)
    vh=max(float(meta.get("virtual_height") or cap_native_h),1)
    desktop_x=max(0.0,min((abs_x-vx)/vw,1.0))
    desktop_y=max(0.0,min((abs_y-vy)/vh,1.0))
    corr=_uia_correlation_for_point(abs_x,abs_y,state)
    return {
        "found":True,"target":target,"label":str(obj.get("label") or target)[:240],
        "confidence":round(conf,3),"capture_x":round(x,6),"capture_y":round(y,6),
        "desktop_x":round(desktop_x,6),"desktop_y":round(desktop_y,6),
        "width":round(w,6),"height":round(h,6),
        "absolute_x":round(abs_x,1),"absolute_y":round(abs_y,1),
        "evidence":str(obj.get("evidence") or "")[:1200],
        "uia_correlation":corr,"capture":meta
    }

def vision_target_click(target,kind="click",scope="active_window"):
    """Visually locate a target, then click its verified center using desktop coordinates."""
    kind=str(kind or "click").strip().lower()
    if kind not in {"click","double","right"}:
        raise ValueError("Unsupported visual click type.")
    hit=vision_locate_target(target,scope)
    if not hit.get("found"):
        raise RuntimeError(hit.get("evidence") or f"I could not visually locate {target}.")
    before=bridge.screen_fingerprint()
    pointer=bridge.pointer_action(hit["desktop_x"],hit["desktop_y"],kind)
    time.sleep(0.5)
    after=bridge.screen_fingerprint()
    hit["pointer"]=pointer
    hit["kind"]=kind
    hit["screen_changed"]=before.get("sha256")!=after.get("sha256")
    return hit

def wait_for_visual_state(description,timeout=12,interval=1.5):
    """Poll screenshot vision until a described visible state is confidently present."""
    description=str(description or "").strip()
    if not description:
        raise ValueError("A visual state description is required.")
    timeout=max(1.0,min(float(timeout),30.0))
    interval=max(0.75,min(float(interval),5.0))
    deadline=time.time()+timeout
    attempts=[]
    while time.time()<deadline:
        hit=vision_locate_target(description,"desktop",0.58)
        attempts.append({"found":hit.get("found"),"confidence":hit.get("confidence"),"evidence":hit.get("evidence")})
        if hit.get("found"):
            hit["attempts"]=len(attempts)
            return hit
        time.sleep(interval)
    return {"found":False,"target":description,"attempts":len(attempts),"checks":attempts[-4:]}


def compact_desktop_state(include_ocr=True):
    try:
        snap=bridge.ui_snapshot(100)
    except Exception as e:
        snap={"error":str(e),"window":"","process":"","elements":[]}
    controls=[]
    for e in (snap.get("elements") or []):
        if e.get("offscreen") or not e.get("name"):
            continue
        controls.append({
            "type":e.get("type",""),"name":str(e.get("name",""))[:180],
            "id":str(e.get("id",""))[:100],"enabled":e.get("enabled",True),
            "toggle":e.get("toggle",""),
            "rect":e.get("rect",[0,0,0,0])
        })
        if len(controls)>=65: break
    try:
        windows=bridge.list_visible_windows(14)
    except Exception:
        windows=[]
    ocr={"available":False,"text":"","lines":[]}
    if include_ocr:
        try:
            raw=bridge.screen_ocr(1450,30,240)
            ocr={
                "available":raw.get("available",False),
                "text":str(raw.get("text") or "")[:9000],
                "lines":[
                    {"text":str(x.get("text",""))[:220],"x":x.get("x"),"y":x.get("y"),"conf":x.get("conf")}
                    for x in (raw.get("lines") or [])[:90]
                ],
                "fingerprint":raw.get("fingerprint","")
            }
        except Exception as e:
            ocr={"available":False,"error":str(e),"text":"","lines":[]}
    try:
        fp=bridge.screen_fingerprint()
    except Exception:
        fp={}
    return {
        "window":snap.get("window",""),"process":snap.get("process",""),
        "controls":controls,"windows":windows[:14],"ocr":ocr,"screen":fp
    }

def _summary_from_partial_json(text):
    s=str(text or "")
    marker='"summary"'
    i=s.find(marker)
    if i<0:
        return ""
    colon=s.find(":",i+len(marker))
    if colon<0:
        return ""
    q=s.find('"',colon+1)
    if q<0:
        return ""
    escaped=False
    for j in range(q+1,len(s)):
        ch=s[j]
        if escaped:
            escaped=False
            continue
        if ch=="\\":
            escaped=True
            continue
        if ch=='"':
            try:
                return json.loads(s[q:j+1])
            except Exception:
                return s[q+1:j]
    return ""

def describe_visual_screen(request_text="Describe what is visibly on the current screen."):
    state=compact_desktop_state(True)
    raw,*_=bridge.screen_jpeg(1100,52)
    system=(
        "You are a Windows screen-vision assistant. Inspect the actual screenshot pixels. "
        "Use OCR/UI context as supporting evidence, not as a replacement for vision. "
        "Describe visible text, dialogs, warnings, status, progress, icons or visual content relevant to the request. "
        "Do not invent hidden content. Return ONLY JSON: "
        '{"summary":"plain-language answer","visible_text":["important visible text"],'
        '"warnings":["visible warnings/errors"],"targets":[{"label":"visual target","x":0.5,"y":0.5,"confidence":0.9}]}.'
    )
    prompt=(
        "REQUEST:\n"+str(request_text)+
        "\n\nSTRUCTURED UI + PIXEL OCR CONTEXT:\n"+json.dumps(state,ensure_ascii=False)[:18000]
    )
    try:
        answer=omni_vision_chat(prompt,system,1600,raw,timeout=60)
        try:
            obj=parse_json_object(answer)
            summary=str(obj.get("summary") or "").strip()
        except Exception:
            summary=_summary_from_partial_json(answer)
            obj={"visible_text":[],"warnings":["Vision response was truncated after the summary."],"targets":[],"raw_vision":answer[:6000]}
        if not summary:
            summary=bridge.clean_for_speech(answer[:3500])
        obj["summary"]=summary
        obj["spoken"]=bridge.clean_for_speech(summary)
        obj["source"]="screenshot+vision+ocr+uia"
        return obj
    except Exception as vision_error:
        ocr_text=((state.get("ocr") or {}).get("text") or "")
        fallback=summarize_text(
            ocr_text or active_window_text(),
            "Explain what is visibly readable on the current screen for this request: "+str(request_text)
        )
        return {
            "summary":fallback,"spoken":bridge.clean_for_speech(fallback),
            "visible_text":[ocr_text[:5000]] if ocr_text else [],
            "warnings":[f"Vision model unavailable; used pixel OCR and UI data: {vision_error}"],
            "targets":[],"source":"screenshot-ocr+uia-fallback"
        }

def _history_for_prompt(history):
    out=[]
    for h in (history or [])[-6:]:
        item={
            "step":h.get("step"),"action":h.get("action"),
            "spoken":str(h.get("spoken",""))[:600],
            "screen_changed":h.get("screen_changed"),
            "expected":str(h.get("expected",""))[:500],
            "verification":h.get("verification")
        }
        data=h.get("data")
        if data is not None:
            try:item["data"]=json.loads(json.dumps(data,ensure_ascii=False)[:7000])
            except:item["data"]=str(data)[:3000]
        out.append(item)
    return out

def universal_plan_step(goal,history=None):
    state=compact_desktop_state(True)
    raw,*_=bridge.screen_jpeg(1150,52)
    prompt=(
        "OVERALL USER GOAL:\n"+str(goal)+
        "\n\nCURRENT DESKTOP STATE:\n"+json.dumps(state,ensure_ascii=False)[:24000]+
        "\n\nRECENT EXECUTED STEPS:\n"+json.dumps(_history_for_prompt(history),ensure_ascii=False)[:16000]+
        "\n\nInspect the CURRENT screenshot. Decide whether the goal is already complete. "
        "If not, choose exactly one next action."
    )
    try:
        raw_answer=omni_vision_chat(prompt,UNIVERSAL_SYSTEM,1200,raw,timeout=60)
        return parse_json_object(raw_answer),state
    except Exception as vision_error:
        # Pixel OCR is still direct screen perception. Fall back to text planning over OCR + UI.
        fallback_prompt=(
            prompt+"\n\nVISION MODEL ERROR:\n"+str(vision_error)+
            "\nUse the PIXEL OCR and UI data above to choose one safe next action."
        )
        raw_answer=omni_chat(fallback_prompt,UNIVERSAL_SYSTEM,900)
        return parse_json_object(raw_answer),state

UNIVERSAL_VERIFY_SYSTEM = r"""
You are the verifier for a Windows desktop-control agent. You can SEE the screenshot after an action
and you also receive UI Automation state, OCR read from screen pixels, visible windows, the action result,
and the user's overall goal.

Return ONLY one JSON object:
{"action_succeeded":true,"goal_complete":false,"needs_replan":false,"summary":"short factual result","evidence":"specific visible or system evidence"}

Rules:
- Be conservative. Never mark goal_complete unless the CURRENT evidence proves the entire user goal is complete.
- action_succeeded means the immediately preceding action achieved its stated expected effect.
- Use visible screenshot evidence first when the action concerns visible UI.
- Use structured/native action data for filesystem, process, service, clipboard, network, or other non-visual actions.
- If the active window/title contradicts the expected result, action_succeeded must be false.
- A changed screenshot alone is not proof of success.
- If evidence is ambiguous, action_succeeded=false and needs_replan=true.
- Do not invent text, controls, files, windows, or state that are not present in the supplied evidence.
"""

def verify_universal_step(goal,action,expected,action_spoken,action_data,before_fp=None):
    state=compact_desktop_state(True)
    try:
        raw,*_=bridge.screen_jpeg(1050,50)
    except Exception:
        raw=None
    after_fp=state.get("screen") or {}
    changed=bool(before_fp and before_fp.get("sha256") and after_fp.get("sha256") and before_fp.get("sha256")!=after_fp.get("sha256"))
    evidence={
        "goal":str(goal)[:1800],
        "action":action,
        "expected":str(expected or "")[:1200],
        "action_spoken":str(action_spoken or "")[:800],
        "action_data":action_data,
        "screen_changed":changed,
        "current_state":state
    }
    prompt="VERIFY THE LAST ACTION AND THE OVERALL GOAL:\n"+json.dumps(evidence,ensure_ascii=False)[:36000]
    try:
        if raw is not None:
            answer=omni_vision_chat(prompt,UNIVERSAL_VERIFY_SYSTEM,650,raw,timeout=45)
        else:
            answer=omni_chat(prompt,UNIVERSAL_VERIFY_SYSTEM,650)
        obj=parse_json_object(answer)
        result={
            "action_succeeded":bool(obj.get("action_succeeded",False)),
            "goal_complete":bool(obj.get("goal_complete",False)),
            "needs_replan":bool(obj.get("needs_replan",False)),
            "summary":str(obj.get("summary") or "").strip()[:1200],
            "evidence":str(obj.get("evidence") or "").strip()[:2200],
            "screen_changed":changed,
            "state":{"window":state.get("window",""),"process":state.get("process",""),"screen":after_fp},
            "source":"screenshot+vision+uia+ocr+native"
        }
        return result
    except Exception as verify_error:
        # Conservative fallback: native actions with explicit successful result can be accepted,
        # but visible-UI actions are not considered verified merely because pixels changed.
        name=str((action or {}).get("name") or "")
        native_ok=name.startswith("native_") or name in {"clipboard_read","clipboard_write","status","media_key","set_sleep_timeout"}
        return {
            "action_succeeded":bool(native_ok and action_data is not None),
            "goal_complete":False,
            "needs_replan":True,
            "summary":"Verification model was unavailable; I will re-observe before continuing.",
            "evidence":str(verify_error)[:1200],
            "screen_changed":changed,
            "state":{"window":state.get("window",""),"process":state.get("process",""),"screen":after_fp},
            "source":"conservative-structured-fallback"
        }

def _task_update(task_id,**fields):
    if not task_id:
        return
    with TASK_LOCK:
        task=ACTIVE_TASKS.get(task_id)
        if task is not None:
            task.update(fields)
            task["updated"]=time.time()

def task_is_cancelled(task_id):
    with TASK_LOCK:
        return bool(ACTIVE_TASKS.get(task_id,{}).get("cancelled"))

def active_task_snapshot():
    with TASK_LOCK:
        rows=[]
        for tid,task in ACTIVE_TASKS.items():
            rows.append({
                "task_id":tid,
                "goal":str(task.get("goal",""))[:800],
                "status":str(task.get("status","running")),
                "step":int(task.get("step",0) or 0),
                "message":str(task.get("message",""))[:800],
                "action":task.get("action"),
                "expected":str(task.get("expected",""))[:800],
                "cancelled":bool(task.get("cancelled",False)),
                "started":task.get("started"),
                "updated":task.get("updated"),
            })
        return rows

def cancel_universal_tasks():
    count=0
    with TASK_LOCK:
        for task in ACTIVE_TASKS.values():
            if not task.get("cancelled"):
                task["cancelled"]=True
                count+=1
    return count

def run_universal_task(goal,task_id=None,history=None,confirmed_action=None):
    goal=str(goal or "").strip()
    if not goal:
        return {"status":"error","spoken":"I need a goal to control the PC."}
    history=list(history or [])
    if not task_id:
        task_id=uuid.uuid4().hex
    with TASK_LOCK:
        task=ACTIVE_TASKS.setdefault(task_id,{"goal":goal,"cancelled":False,"started":time.time()})
        task["goal"]=goal
        task["status"]="starting"
        task["step"]=len(history)
        task["message"]="Preparing the current desktop state."
        task["updated"]=time.time()

    if confirmed_action is not None:
        try:
            before=bridge.screen_fingerprint()
            spoken,data=execute(confirmed_action)
            time.sleep(0.85)
            verification=verify_universal_step(
                goal,confirmed_action,"confirmed consequential action",spoken,data,before
            )
            history.append({
                "step":len(history)+1,"action":confirmed_action,"spoken":spoken,"data":data,
                "expected":"confirmed consequential action",
                "screen_changed":verification.get("screen_changed",False),
                "verification":verification
            })
            if verification.get("goal_complete"):
                ACTIVE_TASKS.pop(task_id,None)
                final=verification.get("summary") or spoken or "The task is complete."
                return {"status":"ok","spoken":bridge.clean_for_speech(final),"task_id":task_id,
                        "data":{"history":history,"verification":verification}}
        except Exception as e:
            return {"status":"error","spoken":f"I could not complete the confirmed action. {e}","task_id":task_id}

    for _ in range(UNIVERSAL_MAX_STEPS):
        with TASK_LOCK:
            if ACTIVE_TASKS.get(task_id,{}).get("cancelled"):
                ACTIVE_TASKS.pop(task_id,None)
                return {"status":"cancelled","spoken":"Stopped the current PC task.","task_id":task_id}
        _task_update(
            task_id,
            status="observing",
            step=len(history)+1,
            message="Looking at the current screen and system state.",
            action=None,
            expected=""
        )
        try:
            decision,state=universal_plan_step(goal,history)
        except Exception as e:
            ACTIVE_TASKS.pop(task_id,None)
            return {"status":"error","spoken":f"I could not inspect and plan the next PC action. {e}","task_id":task_id}
        if task_is_cancelled(task_id):
            ACTIVE_TASKS.pop(task_id,None)
            return {"status":"cancelled","spoken":"Stopped the current PC task.","task_id":task_id}
        status=str(decision.get("status") or "continue").strip().lower()
        reply=str(decision.get("reply") or "").strip()
        expected=str(decision.get("expected") or "").strip()
        if status=="done":
            ACTIVE_TASKS.pop(task_id,None)
            spoken=bridge.clean_for_speech(reply or "The task is complete.")
            return {"status":"ok","spoken":spoken,"task_id":task_id,"data":{"history":history,"final_state":{"window":state.get("window"),"process":state.get("process"),"screen":state.get("screen")}}}
        if status=="blocked":
            ACTIVE_TASKS.pop(task_id,None)
            return {"status":"blocked","spoken":bridge.clean_for_speech(reply or "I am blocked on the current screen."),"task_id":task_id,"data":{"history":history}}
        action=decision.get("action")
        norm=normalize_plan({"reply":reply or "Continuing.","action":action})
        action=norm.get("action")
        if not action:
            ACTIVE_TASKS.pop(task_id,None)
            return {"status":"blocked","spoken":bridge.clean_for_speech(norm.get("reply") or "I need more information."),"task_id":task_id,"data":{"history":history}}
        if risk_for(action)=="confirm":
            _task_update(
                task_id,
                status="waiting_confirmation",
                message=reply or "This next step needs approval.",
                action=action,
                expected=expected
            )
            return {
                "status":"confirm",
                "spoken":bridge.clean_for_speech((reply or "This next step needs approval.")+" Confirm on the phone to continue."),
                "task_id":task_id,"action":action,
                "universal":{"goal":goal,"task_id":task_id,"history":history,"expected":expected}
            }
        try:
            _task_update(
                task_id,
                status="acting",
                message=reply or f"Running {action.get('name','the next action')}.",
                action=action,
                expected=expected
            )
            before=bridge.screen_fingerprint()
            spoken,data=execute(action)
            wait=1.55 if action.get("name") in {"open_app","open_start_app","browser_open_url","browser_search","open_this_pc","open_drive","open_known_location","ui_invoke","screen_click_text","vision_click"} else 0.75
            time.sleep(wait)
            _task_update(
                task_id,
                status="verifying",
                message="Checking the screen and system state to verify the action.",
                action=action,
                expected=expected
            )
            verification=verify_universal_step(goal,action,expected,spoken,data,before)
            history.append({
                "step":len(history)+1,"action":action,"spoken":spoken,"data":data,
                "expected":expected,
                "screen_changed":verification.get("screen_changed",False),
                "verification":verification
            })
            set_state("last_action",action)
            set_state("last_verification",verification)
            _task_update(
                task_id,
                status="verified" if verification.get("action_succeeded") else "replanning",
                message=verification.get("summary") or (
                    "Action verified." if verification.get("action_succeeded")
                    else "The expected result was not verified; replanning."
                ),
                action=action,
                expected=expected
            )
            if verification.get("goal_complete"):
                ACTIVE_TASKS.pop(task_id,None)
                final=verification.get("summary") or spoken or "The task is complete."
                return {"status":"ok","spoken":bridge.clean_for_speech(final),"task_id":task_id,
                        "data":{"history":history,"verification":verification,
                                "final_state":verification.get("state")}}
            # Always loop back through a fresh screenshot/state. If verification
            # failed, the planner sees that evidence and must choose an alternate method.
        except Exception as e:
            history.append({"step":len(history)+1,"action":action,"spoken":f"Action failed: {e}","data":{"error":str(e)},"expected":expected,"screen_changed":False})
            # Do not abort immediately: the next observation lets vision choose an alternate method.
            continue

    ACTIVE_TASKS.pop(task_id,None)
    return {
        "status":"blocked",
        "spoken":"I reached the safe step limit before I could verify the whole task. I stopped instead of continuing blindly.",
        "task_id":task_id,"data":{"history":history}
    }

app=FastAPI(title="SHAL Voice PC Agent",version="0.4.0")

def check_key(x_pc_agent_key: Optional[str]):
    if not x_pc_agent_key or not secrets.compare_digest(x_pc_agent_key,CONFIG["pairing_key"]):
        raise HTTPException(status_code=401,detail="Invalid pairing key")

@app.get("/health")
def health():
    return {
        "ok":True,
        "pc":os.environ.get("COMPUTERNAME","SHAL"),
        "tailscale_ip":get_tailscale_ip(),
        "agent_version":"0.4.0",
        "capabilities":{
            "screen_vision":True,
            "uia":True,
            "keyboard_mouse":True,
            "native_windows":True,
            "observe_plan_act_verify":True
        }
    }

@app.get("/status")
def status(x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    return system_status()

@app.get("/history")
def history(x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    with db() as con:
        rows=con.execute("SELECT ts,user_text,action,result,success FROM events ORDER BY id DESC LIMIT 40").fetchall()
    return [{"ts":r[0],"text":r[1],"action":r[2],"result":r[3],"success":bool(r[4])} for r in rows]

@app.get("/agent/task")
def agent_task(x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    return {"tasks":active_task_snapshot()}

@app.post("/agent/cancel")
def agent_cancel(x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    count=cancel_universal_tasks()
    return {"ok":True,"cancelled":count,"spoken":"Stopping the current PC task." if count else "There is no active PC task."}

@app.get("/agent/observe")
def agent_observe(
    vision: bool=False,
    request_text: str="Describe what is visibly on the current screen.",
    x_pc_agent_key: Optional[str]=Header(default=None)
):
    check_key(x_pc_agent_key)
    if vision:
        return describe_visual_screen(request_text)
    return compact_desktop_state(True)

@app.get("/screen")
def screen(x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    try:
        data,ow,oh,w,h=bridge.screen_jpeg()
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={
                "X-Screen-Original-Width":str(ow),
                "X-Screen-Original-Height":str(oh),
                "X-Screen-Width":str(w),
                "X-Screen-Height":str(h),
                "Cache-Control":"no-store"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500,detail=f"Screen capture failed: {e}")

@app.post("/screen/pointer")
def screen_pointer(req: PointerRequest, x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    kind=req.kind if req.kind in {"click","double","right"} else "click"
    return {"ok":True,"data":bridge.pointer_action(req.x,req.y,kind)}

@app.post("/screen/scroll")
def screen_scroll(req: ScrollRequest, x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    delta=max(-8,min(int(req.delta),8))
    return {"ok":True,"data":bridge.scroll_screen(delta)}

@app.post("/speech")
def speech(req: SpeechRequest, x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    try:
        audio=bridge.synthesize_piper(req.text)
        return Response(content=audio,media_type="audio/wav",headers={"Cache-Control":"no-store"})
    except Exception as e:
        raise HTTPException(status_code=500,detail=f"Speech synthesis failed: {e}")

@app.post("/agent/command")
def command(req: CommandRequest, request: Request, x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    text=req.text.strip()
    if not text:
        raise HTTPException(status_code=400,detail="Empty command")
    low=text.lower().strip(" .!?")
    if low in {"stop","cancel","stop current task","cancel current task","stop doing that","pause current task"}:
        count=cancel_universal_tasks()
        spoken="Stopped the current PC task." if count else "There is no active PC task to stop."
        event(text,"cancel_universal",spoken,True)
        return {"status":"cancelled","spoken":spoken,"action":None}
    try:
        plan=local_rule(text)
        # Unknown goals and multi-step requests use the autonomous visual loop.
        if plan is None or ((plan.get("action") or {}).get("name") in {"voice_sequence","desktop_sequence"}):
            result=run_universal_task(text)
            if result.get("status")=="confirm":
                cid=uuid.uuid4().hex
                PENDING[cid]={
                    "created":time.time(),"text":text,"action":result["action"],
                    "universal":result.get("universal") or {}
                }
                event(text,result["action"].get("name","universal"),"confirmation requested",True)
                return {
                    "status":"confirm","spoken":result.get("spoken","Confirm on the phone to continue."),
                    "confirmation_id":cid,"action":result["action"],"task_id":result.get("task_id")
                }
            event(text,"universal",result.get("spoken",""),result.get("status") not in {"error"})
            return result

        plan=normalize_plan(plan)
        action=plan.get("action")
        if action is None:
            event(text,"none",plan["reply"],True)
            return {"status":"ok","spoken":plan["reply"],"action":None}
        if should_use_universal(action):
            result=run_universal_task(text)
            if result.get("status")=="confirm":
                cid=uuid.uuid4().hex
                PENDING[cid]={
                    "created":time.time(),"text":text,"action":result["action"],
                    "universal":result.get("universal") or {}
                }
                event(text,result["action"].get("name","universal"),"confirmation requested",True)
                return {
                    "status":"confirm","spoken":result.get("spoken","Confirm on the phone to continue."),
                    "confirmation_id":cid,"action":result["action"],"task_id":result.get("task_id")
                }
            event(text,"universal",result.get("spoken",""),result.get("status") not in {"error"})
            return result
        if risk_for(action)=="confirm":
            cid=uuid.uuid4().hex
            PENDING[cid]={"created":time.time(),"text":text,"action":action}
            spoken=plan["reply"]+" Confirm on the phone to continue."
            event(text,action["name"],"confirmation requested",True)
            return {"status":"confirm","spoken":spoken,"confirmation_id":cid,"action":action}
        try:
            spoken,data=execute(action)
            remember_result(action,spoken,data)
            event(text,action["name"],spoken,True)
            return {"status":"ok","spoken":spoken,"action":action,"data":data}
        except Exception as direct_error:
            # If a simple UI action failed, re-observe the real screen and recover with the universal loop.
            if action.get("name") in {"ui_invoke","ui_set_toggle","ui_set_text","focus_window","open_start_app","screen_click_text","vision_click"}:
                result=run_universal_task(text)
                if result.get("status")=="confirm":
                    cid=uuid.uuid4().hex
                    PENDING[cid]={
                        "created":time.time(),"text":text,"action":result["action"],
                        "universal":result.get("universal") or {}
                    }
                    return {
                        "status":"confirm","spoken":result.get("spoken","Confirm on the phone to continue."),
                        "confirmation_id":cid,"action":result["action"],"task_id":result.get("task_id")
                    }
                return result
            raise direct_error
    except Exception as e:
        log(f"COMMAND ERROR text={text!r} error={e}")
        event(text,"error",str(e),False)
        return {"status":"error","spoken":f"I could not complete that. {e}"}

@app.post("/agent/confirm")
def confirm(req: ConfirmRequest, x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    item=PENDING.pop(req.confirmation_id,None)
    if not item or time.time()-item["created"]>120:
        raise HTTPException(status_code=410,detail="Confirmation expired")
    try:
        universal=item.get("universal") or {}
        if universal:
            result=run_universal_task(
                universal.get("goal") or item.get("text",""),
                task_id=universal.get("task_id"),
                history=universal.get("history") or [],
                confirmed_action=item["action"]
            )
            if result.get("status")=="confirm":
                cid=uuid.uuid4().hex
                PENDING[cid]={
                    "created":time.time(),"text":item.get("text",""),
                    "action":result["action"],"universal":result.get("universal") or {}
                }
                return {
                    "status":"confirm","spoken":result.get("spoken","Confirm on the phone to continue."),
                    "confirmation_id":cid,"action":result["action"],"task_id":result.get("task_id")
                }
            event(item.get("text",""),"universal",result.get("spoken",""),result.get("status")!="error")
            return result
        if item["action"].get("name")=="voice_sequence":
            item["action"].setdefault("args",{})["_confirmed"]=True
        spoken,data=execute(item["action"])
        remember_result(item["action"],spoken,data)
        event(item["text"],item["action"]["name"],spoken,True)
        return {"status":"ok","spoken":spoken,"data":data}
    except Exception as e:
        event(item.get("text",""),item.get("action",{}).get("name","confirm"),str(e),False)
        return {"status":"error","spoken":f"I could not complete that. {e}"}

@app.post("/power/cancel")
def cancel_power(x_pc_agent_key: Optional[str]=Header(default=None)):
    check_key(x_pc_agent_key)
    cp=subprocess.run(["shutdown.exe","/a"],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    msg=cp.stdout.strip() or cp.stderr.strip() or "Cancel request sent."
    event("cancel power","cancel_power",msg,cp.returncode==0)
    return {"ok":cp.returncode==0,"spoken":msg}
