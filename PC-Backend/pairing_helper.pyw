import tkinter as tk
from tkinter import ttk
import agent_backend as backend

root=tk.Tk()
root.title("Voice PC Agent Pairing")
root.geometry("620x300")
root.resizable(False,False)

ip=backend.get_tailscale_ip()
port=backend.CONFIG.get("port",8765)
key=backend.CONFIG["pairing_key"]

ttk.Label(root,text="SHAL Voice PC Agent",font=("Segoe UI Semibold",18)).pack(pady=(18,6))
ttk.Label(root,text="Use these values once in the Android app. Keep the pairing key private.").pack(pady=(0,14))

frame=ttk.Frame(root,padding=10); frame.pack(fill="x",padx=18)
ttk.Label(frame,text="Server").grid(row=0,column=0,sticky="w",pady=5)
server=tk.StringVar(value=f"http://{ip}:{port}")
ttk.Entry(frame,textvariable=server,width=62,state="readonly").grid(row=0,column=1,padx=8)
ttk.Button(frame,text="Copy",command=lambda: (root.clipboard_clear(),root.clipboard_append(server.get()))).grid(row=0,column=2)

ttk.Label(frame,text="Pairing key").grid(row=1,column=0,sticky="w",pady=5)
secret=tk.StringVar(value=key)
entry=ttk.Entry(frame,textvariable=secret,width=62,show="•",state="readonly")
entry.grid(row=1,column=1,padx=8)
def copy_key():
    root.clipboard_clear(); root.clipboard_append(key)
ttk.Button(frame,text="Copy",command=copy_key).grid(row=1,column=2)

ttk.Label(root,text="The app communicates only with this PC backend; ChatGPT is not required.").pack(pady=18)
root.mainloop()
