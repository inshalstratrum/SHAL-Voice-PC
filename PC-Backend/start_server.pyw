import sys, time, threading
from pathlib import Path
import uvicorn
import agent_backend as backend

if __name__=="__main__":
    stream=open(Path(__file__).resolve().parent/"logs"/"server-runtime.log","a",encoding="utf-8",buffering=1)
    sys.stdout=stream
    sys.stderr=stream

    attempts=0
    host=backend.get_tailscale_ip()
    while host=="127.0.0.1":
        if attempts==0 or attempts%12==0:
            backend.log("Voice Agent is waiting for a Tailscale IPv4 address before opening the private API.")
        attempts+=1
        time.sleep(5)
        host=backend.get_tailscale_ip()

    def warm_omniroute():
        try:
            ok=backend.ensure_omniroute_server()
            backend.log("OmniRoute background initialization: "+("ready" if ok else "not ready; will retry on first AI request"))
        except Exception as e:
            backend.log(f"OmniRoute background initialization error: {e}")

    threading.Thread(target=warm_omniroute,daemon=True).start()
    backend.log(f"Starting private Tailscale API on {host}:{backend.CONFIG.get('port',8765)}")
    uvicorn.run(
        backend.app,
        host=host,
        port=int(backend.CONFIG.get("port",8765)),
        log_config=None,
        access_log=False
    )
