"""Windows-only integration check: native move must persist during 3D rendering."""
import ctypes
from ctypes import wintypes
import threading
import time
from app import App
from core import read_config

user=ctypes.WinDLL('user32',use_last_error=True)
user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]

app=App(read_config())
app.window.geometry('1100x760+100+100')
result={}

def rect(hwnd):
    r=wintypes.RECT();user.GetWindowRect(hwnd,ctypes.byref(r));return (r.left,r.top,r.right,r.bottom)

def start():
    hwnd=user.GetAncestor(app.window.winfo_id(),2)
    result['before']=rect(hwnd)
    # Native Windows keyboard move mode, same modal move loop as title dragging.
    def keys():
        time.sleep(.15)
        for key in [0x27]*6+[0x28]*4+[0x0D]:
            user.PostMessageW(hwnd,0x0100,key,0)
            user.PostMessageW(hwnd,0x0101,key,0)
            time.sleep(.045)
    threading.Thread(target=keys,daemon=True).start()
    user.PostMessageW(hwnd,0x0112,0xF010,0)
    app.window.after(1000,lambda:capture_after(hwnd))

def capture_after(hwnd):
    result['after']=rect(hwnd)
    app.window.after(500,lambda:finish(hwnd))

def finish(hwnd):
    result['settled']=rect(hwnd)
    result['ticks']=app.world.tick
    print(result,flush=True)
    app.close()

app.window.after(700,start)
app.window.after(8000,app.close)
app.window.mainloop()
assert result.get('before')!=result.get('after'),'Native move did not change window position'
assert result['after']==result['settled'],'Window snapped back after native move'
