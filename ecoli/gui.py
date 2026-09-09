"""Observer for sequential frozen-policy bacterial trials."""
import datetime
import json
import math
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from .core import DT,DEFAULT
from .trials import Trials,seeds_from_text

ROOT=Path(__file__).resolve().parents[1]

class App:
    def __init__(self):
        self.window=tk.Tk();self.window.title('E. coli / seed trials / 学習OFF');self.window.geometry('1250x860')
        self.settings=json.loads((Path(__file__).with_name('config.json')).read_text(encoding='utf-8'));self.trials=None;self.paused=False;self.trail=[];self.history=[];self.accumulator=0.;self.last=time.perf_counter()
        bar=ttk.Frame(self.window,padding=8);bar.pack(fill='x')
        self.seeds=tk.StringVar(value='0,1,2,3,4,5,6,7,8,9');self.seconds=tk.StringVar(value='60');self.layout=tk.StringVar(value='7');self.speed=tk.DoubleVar(value=1)
        for label,var,width in [('個体seed一覧',self.seeds,28),('各個体の秒数',self.seconds,7),('配置seed',self.layout,8)]:
            ttk.Label(bar,text=label).pack(side='left',padx=4);ttk.Entry(bar,textvariable=var,width=width).pack(side='left')
        ttk.Button(bar,text='新規実行',command=self.start).pack(side='left',padx=5)
        ttk.Button(bar,text='停止 / 再開',command=self.pause).pack(side='left')
        ttk.Button(bar,text='設定JSON',command=self.load).pack(side='left',padx=5)
        ttk.Label(bar,text='速度').pack(side='left');ttk.Scale(bar,from_=1,to=20,variable=self.speed,length=85).pack(side='left')
        self.layout.set(str(self.settings.get('layout_seed',7)))
        self.field=tk.BooleanVar(value=True)
        ttk.Checkbutton(bar,text='濃度の目安',variable=self.field).pack(side='left')
        envbar=ttk.Frame(self.window,padding=5);envbar.pack(fill='x')
        self.environment=tk.StringVar(value='餌だけ' if self.settings.get('environment')=='food_only' else '混合環境')
        self.random_spawn=tk.BooleanVar(value=self.settings.get('random_spawn',False))
        self.food_count=tk.StringVar(value=str(self.settings.get('food_count',8)))
        ttk.Label(envbar,text='環境').pack(side='left',padx=5)
        ttk.Combobox(envbar,textvariable=self.environment,values=['餌だけ','混合環境'],state='readonly',width=12).pack(side='left')
        ttk.Checkbutton(envbar,text='初期位置・向きをランダム化',variable=self.random_spawn).pack(side='left',padx=10)
        ttk.Label(envbar,text='ランダム配置の餌数').pack(side='left')
        ttk.Entry(envbar,textvariable=self.food_count,width=5).pack(side='left',padx=5)
        ttk.Label(envbar,text='配置seedを変えて新規実行 / 同じ比較内では全個体が同じ配置').pack(side='left',padx=10)
        self.status=tk.StringVar();ttk.Label(self.window,textvariable=self.status,padding=6).pack(fill='x')
        self.canvas=tk.Canvas(self.window,bg='#101923',highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.table=ttk.Treeview(self.window,columns=('seed','seconds','food','damage','path','contact','turns','energy'),show='headings',height=6)
        for key,label in zip(self.table['columns'],('Seed','経過秒','摂食','被ダメージ','距離 m','接触秒','方向転換','残エネルギー')):
            self.table.heading(key,text=label);self.table.column(key,width=120,anchor='center')
        self.table.pack(fill='x');self.output=tk.StringVar();ttk.Label(self.window,textvariable=self.output,padding=6).pack(fill='x')
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<space>',lambda e:self.pause())
        self.window.after(100,self.start);self.window.after(33,self.update)

    def load(self):
        path=filedialog.askopenfilename(filetypes=[('JSON','*.json')])
        if not path:return
        try:
            from .core import config
            settings=json.loads(Path(path).read_text(encoding='utf-8'));config(settings)
            self.settings=settings;self.layout.set(str(settings.get('layout_seed',7)))
            self.environment.set('餌だけ' if settings.get('environment')=='food_only' else '混合環境')
            self.random_spawn.set(settings.get('random_spawn',False));self.food_count.set(str(settings.get('food_count',8)))
            self.output.set('設定を読み込みました。「新規実行」で適用します。')
        except Exception as error:messagebox.showerror('設定エラー',str(error))

    def start(self):
        try:
            seeds=seeds_from_text(self.seeds.get());seconds=float(self.seconds.get());settings=dict(self.settings,layout_seed=int(self.layout.get()),environment='food_only' if self.environment.get()=='餌だけ' else 'mixed',random_spawn=self.random_spawn.get(),food_count=int(self.food_count.get()))
            # Validate world and duration before interrupting an existing run.
            from .core import World
            World(seeds[0],settings)
            if not math.isfinite(seconds) or not DT<=seconds<=3600:raise ValueError('秒数は1/30〜3600')
            if self.trials:self.trials.close()
            path=ROOT/'sessions'/'ecoli'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            self.trials=Trials(seeds,seconds,settings,path)
        except Exception as error:messagebox.showerror('実行できません',str(error));return
        self.table.delete(*self.table.get_children());self.reported=0;self.trail=[];self.history=[];self.paused=False
        self.accumulator=0.;self.last=time.perf_counter();self.output.set('保存先: '+str(path))

    def pause(self):self.paused=not self.paused;self.accumulator=0.
    def text(self,x,y,text,size=12,color='#dce9f3'):
        self.canvas.create_text(x,y,text=text,anchor='nw',fill=color,font=('Yu Gothic UI',size))

    def draw(self):
        if not self.trials:return
        c=self.canvas;c.delete('all');w=self.trials.world;cw=c.winfo_width();ch=c.winfo_height();mapw=cw*.65
        xmin,xmax,ymin,ymax=w.c['bounds'];scale=min((mapw-50)/(xmax-xmin),(ch-55)/(ymax-ymin))
        def pt(x,y):return (25+(x-xmin)*scale,35+(ymax-y)*scale)
        a,b=pt(xmin,ymax);d,e=pt(xmax,ymin);c.create_rectangle(a,b,d,e,fill='#172b34',outline='#8297a8')
        self.text(25,5,'WORLD / '+('餌だけ' if w.c['environment']=='food_only' else '混合環境')+' / 視覚は観察専用',12,'#6be6ca')
        for o in w.objects:
            if o['kind']=='food' and o['remaining']<=0:continue
            x,y=pt(o['x'],o['y']);r=o['radius']*scale
            if o['kind']=='food' and self.field.get():
                for k in (1,2,3):
                    radius=w.c['concentration_scale']*scale*k
                    c.create_oval(x-radius,y-radius,x+radius,y+radius,outline='#264839',dash=(2,6))
            color={'food':'#54b97b','harm':'#b44e66','obstacle':'#788697'}[o['kind']]
            c.create_oval(x-r,y-r,x+r,y+r,fill=color,outline=color)
        if len(self.trail)>1:c.create_line(*[v for p in self.trail for v in pt(*p)],fill='#70dbc8',width=2)
        x,y=pt(w.x,w.y);r=max(5,w.c['body_radius']*scale)
        c.create_oval(x-r,y-r,x+r,y+r,fill='#ffe196',outline='#fff6d1',width=2)
        c.create_line(x,y,x+math.cos(w.heading)*r*2,y-math.sin(w.heading)*r*2,fill='#ffffff',arrow='last',width=2)
        panel=mapw+10;c.create_rectangle(panel-8,0,cw,ch,fill='#101923',outline='')
        self.text(panel,8,'SENSORS / 行動入力',13,'#6be6ca')
        labels=('接触','化学濃度','エネルギー / 100','直前ダメージ / 100','直前摂食 / 100','直前の方向転換')
        for i,(label,v) in enumerate(zip(labels,w.last_inputs)):
            yy=40+i*27;self.text(panel,yy,label,10);self.text(panel+185,yy,f'{v:.3f}',10)
            c.create_rectangle(panel+245,yy+4,panel+245+max(0,v)*80,yy+15,fill='#6be6ca',outline='')
        self.text(panel,212,'NETWORK / 固定ランダム重み・学習OFF',12,'#6be6ca')
        for i,v in enumerate(w.policy.state):
            xx=panel+35+i*75;yy=266;c.create_oval(xx-16,yy-16,xx+16,yy+16,fill='#55bfac' if v>=0 else '#dc839b',outline='')
            self.text(xx-17,yy+21,f'h{i} {v:+.2f}',9)
        self.text(panel,311,f'方向転換確率 {w.policy.probability:.3f} / 行動 {w.action}',11)
        self.text(panel,338,'濃度の履歴（直近約10秒）',10)
        if len(self.history)>1:
            c.create_line(*[v for i,p in enumerate(self.history) for v in (panel+i/299*310,418-p*60)],fill='#f3d37c',width=2)
        self.text(panel,440,f'摂食 {w.food_total:.2f} / 被ダメージ {w.damage_total:.2f}',11)
        self.text(panel,466,f'重み {w.policy.fingerprint[:16]} / 更新0回',9)

    def update(self):
        now=time.perf_counter();elapsed=min(.25,now-self.last);self.last=now
        t=self.trials
        if t:
            if not self.paused and not t.done:
                self.accumulator+=elapsed*self.speed.get()
                while self.accumulator>=DT and not t.done:
                    old=t.world;t.step();self.accumulator-=DT
                    if t.world is not old:self.trail=[];self.history=[]
                    else:
                        self.trail.append((old.x,old.y));self.trail=self.trail[-3000:]
                        self.history.append(old.concentration);self.history=self.history[-300:]
                while self.reported<len(t.results):
                    r=t.results[self.reported];self.reported+=1
                    self.table.insert('', 'end',values=(r['seed'],round(r['elapsed_s'],2),round(r['food'],2),round(r['damage'],2),round(r['path_m'],3),round(r['contact_s'],2),r['tumbles'],round(r['energy'],2)))
            state='完了' if t.done else ('一時停止' if self.paused else '実行中')
            self.status.set(f"{state} / {len(t.results)} / {len(t.seeds)}個体完了 / seed {t.world.seed} / {t.world.tick*DT:.2f} / {t.seconds:.2f}秒 / 学習OFF")
            self.draw()
        self.window.after(33,self.update)
    def close(self):
        if self.trials:self.trials.close()
        self.window.destroy()

if __name__=='__main__':App().window.mainloop()
