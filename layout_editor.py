import copy
import json
import math
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from layout import KINDS,layout_from_config,resolve_layout,integer,finite

LABELS={'obstacle':'障害物','harm':'ダメージ床','food':'餌'}


class LayoutEditor:
    def __init__(self,app):
        self.app=app
        self.layout=layout_from_config(app.config)
        self.window=tk.Toplevel(app.window)
        self.window.title('配置設定 — AI / 障害物 / ダメージ床 / 餌')
        self.window.geometry('1050x690');self.window.minsize(960,650)
        self.window.transient(app.window)
        outer=ttk.Frame(self.window,padding=12);outer.pack(fill='both',expand=True)
        common=ttk.LabelFrame(outer,text='ランダム配置の範囲（m）とシード',padding=8);common.pack(fill='x')
        self.seed=tk.StringVar();self.bounds=[tk.StringVar() for _ in range(4)]
        self.entry(common,'seed',self.seed,0)
        for i,(label,var) in enumerate(zip(('X最小','X最大','Y最小','Y最大'),self.bounds)):self.entry(common,label,var,i+1)
        ttk.Button(common,text='新しいseed',command=self.new_seed).grid(row=0,column=10,padx=5)
        ttk.Label(outer,text='Zは接地面に固定。位置はm、向きは度。設定は「適用してリセット」で反映します。').pack(anchor='w',pady=8)
        body=ttk.Frame(outer);body.pack(fill='both',expand=True)
        book=ttk.Notebook(body);book.pack(side='left',fill='both',expand=True)
        spawn=ttk.Frame(book,padding=10);book.add(spawn,text='AI初期位置')
        self.spawn={k:tk.StringVar() for k in ('x','y','heading_deg')}
        for i,(key,label) in enumerate((('x','後足X'),('y','後足Y'),('heading_deg','向き°'))):
            ttk.Label(spawn,text=label).grid(row=i,column=0,pady=10,sticky='w')
            ttk.Entry(spawn,textvariable=self.spawn[key],width=18).grid(row=i,column=1,padx=10)
        self.spawn_random=tk.BooleanVar()
        ttk.Checkbutton(spawn,text='初期位置XYをランダム化（向きは指定値を維持）',variable=self.spawn_random).grid(row=3,column=0,columnspan=3,pady=15)
        ttk.Label(spawn,text='初期位置は後足のワールド座標です。\nランダム配置時は身体全体が範囲に収まり、\n固定物体と重ならない位置を選びます。').grid(row=4,column=0,columnspan=3,sticky='w')
        self.groups={}
        for kind in KINDS:
            tab=ttk.Frame(book,padding=10);book.add(tab,text=LABELS[kind])
            g={key:tk.StringVar() for key in ('count','count_min','count_max','radius','height')}
            g['random_count']=tk.BooleanVar();g['random_positions']=tk.BooleanVar()
            row=ttk.Frame(tab);row.pack(fill='x')
            self.entry(row,'個数',g['count'],0)
            ttk.Checkbutton(row,text='個数もランダム',variable=g['random_count']).grid(row=0,column=2,padx=6)
            row=ttk.Frame(tab);row.pack(fill='x',pady=6)
            self.entry(row,'最小個数',g['count_min'],0);self.entry(row,'最大個数',g['count_max'],1)
            ttk.Checkbutton(tab,text='位置をランダム化',variable=g['random_positions']).pack(anchor='w',pady=5)
            row=ttk.Frame(tab);row.pack(fill='x',pady=6)
            self.entry(row,'半径 m',g['radius'],0);self.entry(row,'高さ m',g['height'],1)
            ttk.Label(tab,text='固定座標：1行に X, Y（任意で 半径, 高さ も指定可）').pack(anchor='w')
            frame=ttk.Frame(tab);frame.pack(fill='both',expand=True)
            g['text']=tk.Text(frame,width=44,height=10,wrap='none',font=('Consolas',11))
            scroll=ttk.Scrollbar(frame,command=g['text'].yview);g['text'].configure(yscrollcommand=scroll.set)
            g['text'].pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
            ttk.Button(tab,text='個数分の座標を補充',command=lambda k=kind:self.fill_positions(k)).pack(anchor='w',pady=8)
            ttk.Label(tab,text='0個で非表示。個数の上限は種類ごとに100個です。').pack(anchor='w')
            self.groups[kind]=g
        right=ttk.Frame(body,padding=(12,0,0,0));right.pack(side='right',fill='both')
        ttk.Label(right,text='配置プレビュー（上面）').pack(anchor='w')
        self.preview_canvas=tk.Canvas(right,width=350,height=350,bg='#182231',highlightthickness=0);self.preview_canvas.pack()
        self.summary=tk.StringVar()
        ttk.Label(right,textvariable=self.summary,wraplength=345).pack(anchor='w',pady=8)
        ttk.Label(right,text='緑: 餌 / 赤: ダメージ床 / 灰: 障害物\n水色矢印: AIの初期位置と向き\nランダム物体は身体・他物体との重複を避けます。\n固定座標の重複は接触テスト用に許可します。',wraplength=345).pack(anchor='w',pady=8)
        buttons=ttk.Frame(outer);buttons.pack(fill='x',pady=(12,0))
        for label,command in [('JSON読込',self.load),('JSON保存',self.save),('プレビュー',self.preview),('適用してリセット',self.apply),('閉じる',self.window.destroy)]:
            ttk.Button(buttons,text=label,command=command).pack(side='left',padx=5)
        self.populate(self.layout);self.preview()

    def entry(self,parent,label,variable,column):
        ttk.Label(parent,text=label).grid(row=0,column=column*2,padx=(0,4))
        ttk.Entry(parent,textvariable=variable,width=9).grid(row=0,column=column*2+1,padx=(0,8))

    def populate(self,layout):
        self.seed.set(str(layout['seed']))
        for var,value in zip(self.bounds,layout['bounds']):var.set(str(value))
        for key,var in self.spawn.items():var.set(str(layout['spawn'].get(key,0)))
        self.spawn_random.set(layout['spawn'].get('randomize',False))
        for kind,g in self.groups.items():
            src=layout['groups'][kind]
            for key in ('count','count_min','count_max','radius','height','random_count','random_positions'):g[key].set(src.get(key, {'count_min':0,'count_max':src['count'],'random_count':False,'random_positions':False}.get(key)))
            g['text'].delete('1.0','end')
            g['text'].insert('1.0','\n'.join(', '.join(str(p[k]) for k in ('x','y','radius','height') if k in p) for p in src['positions']))

    def collect(self):
        result={'seed':integer(self.seed.get(),'seed',0,2147483647),'bounds':[finite(v.get(),'範囲') for v in self.bounds],
            'spawn':{k:finite(v.get(),k) for k,v in self.spawn.items()},'groups':{}}
        result['spawn']['randomize']=self.spawn_random.get()
        for kind,g in self.groups.items():
            group={k:integer(g[k].get(),LABELS[kind]+' '+k) for k in ('count','count_min','count_max')}
            group.update({k:finite(g[k].get(),k) for k in ('radius','height')})
            group.update({k:g[k].get() for k in ('random_count','random_positions')})
            positions=[]
            for number,line in enumerate(g['text'].get('1.0','end').splitlines(),1):
                if not line.strip():continue
                values=line.replace('，',',').split(',')
                if len(values) not in (2,4):raise ValueError(f'{LABELS[kind]} {number}行目: X,Y または X,Y,半径,高さを入力してください')
                positions.append({k:finite(v,f'{LABELS[kind]} {number}行目') for k,v in zip(('x','y','radius','height'),values)})
            group['positions']=positions;result['groups'][kind]=group
        return result

    def report(self,error):messagebox.showerror('配置設定を確認してください',str(error),parent=self.window)

    def fill_positions(self,kind):
        try:
            g=self.groups[kind]
            count=integer(g['count_max' if g['random_count'].get() else 'count'].get(),'個数')
            lines=[line for line in g['text'].get('1.0','end').splitlines() if line.strip()]
            xmin,xmax,ymin,ymax=[finite(v.get(),'範囲') for v in self.bounds]
            if xmin>=xmax or ymin>=ymax:raise ValueError('配置範囲を確認してください')
            columns=max(1,math.ceil(math.sqrt(count)))
            for i in range(len(lines),count):
                x=xmin+(xmax-xmin)*(i%columns+.5)/columns
                y=ymin+(ymax-ymin)*(i//columns+.5)/columns
                lines.append(f'{x:.4f}, {y:.4f}')
            g['text'].delete('1.0','end');g['text'].insert('1.0','\n'.join(lines))
        except (ValueError,tk.TclError) as e:self.report(e)

    def new_seed(self):self.seed.set(str(random.SystemRandom().randrange(2147483648)));self.preview()

    def preview(self):
        try:
            layout=self.collect();spawn,objects=resolve_layout(layout)
            self.draw_preview(layout,spawn,objects)
            return layout
        except (ValueError,KeyError,TypeError,tk.TclError) as e:self.report(e);return None

    def draw_preview(self,layout,spawn,objects):
        c=self.preview_canvas;c.delete('all')
        xmin,xmax,ymin,ymax=layout['bounds']
        angle=math.radians(spawn['heading_deg']);hx=spawn['x']+.27*math.cos(angle);hy=spawn['y']+.27*math.sin(angle)
        xmin=min([xmin,spawn['x'],hx]+[o['x']-o['radius'] for o in objects]);xmax=max([xmax,spawn['x'],hx]+[o['x']+o['radius'] for o in objects])
        ymin=min([ymin,spawn['y'],hy]+[o['y']-o['radius'] for o in objects]);ymax=max([ymax,spawn['y'],hy]+[o['y']+o['radius'] for o in objects])
        scale=310/max(xmax-xmin,ymax-ymin)
        def point(x,y):return 20+(x-xmin)*scale,330-(y-ymin)*scale
        b=layout['bounds'];c.create_rectangle(*point(b[0],b[3]),*point(b[1],b[2]),outline='#8195ae',dash=(4,3))
        for o in objects:
            x,y=point(o['x'],o['y']);r=o['radius']*scale
            c.create_oval(x-r,y-r,x+r,y+r,fill={'food':'#65c99b','harm':'#e76c7e','obstacle':'#8195ae'}[o['kind']],outline='')
        c.create_line(*point(spawn['x'],spawn['y']),*point(hx,hy),fill='#64e7ef',width=4,arrow='last')
        self.summary.set(f"AI: X={spawn['x']:.3f}, Y={spawn['y']:.3f}, 向き={spawn['heading_deg']:.1f}°\n"+' / '.join(f'{LABELS[k]} {sum(o["kind"]==k for o in objects)}個' for k in KINDS)+f"\n配置seed: {layout['seed']}")

    def apply(self):
        layout=self.preview()
        if layout is not None:
            try:self.app.apply_layout(layout)
            except (OSError,ValueError) as e:self.report(e)

    def save(self):
        layout=self.preview()
        if layout is None:return
        path=filedialog.asksaveasfilename(parent=self.window,defaultextension='.json',initialfile='layout.json',filetypes=[('JSON','*.json')])
        if path:
            try:Path(path).write_text(json.dumps(layout,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError as e:self.report(e)

    def load(self):
        path=filedialog.askopenfilename(parent=self.window,filetypes=[('JSON','*.json')])
        if not path:return
        try:
            layout=json.loads(Path(path).read_text(encoding='utf-8'));resolve_layout(layout)
            previous=self.collect()
            try:self.populate(layout)
            except (KeyError,TypeError):self.populate(previous);raise
            self.preview()
        except (OSError,ValueError,KeyError,TypeError) as e:self.report(e)
