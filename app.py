"""Dependency-free desktop observer. Run: python app.py"""
import argparse
import copy
import datetime
import json
import math
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, ttk

from core import World, Recorder, read_config, DT

ROOT = Path(__file__).resolve().parent


class App:
    def __init__(self, config=None, weights=None):
        self.config = config or read_config()
        self.weights = weights
        self.world = World(self.config, weights)
        self.window = tk.Tk()
        self.window.title('Inchworm Tactile | 非視覚・分散AIラボ')
        self.window.geometry('1440x930')
        self.window.minsize(1100, 760)
        self.window.configure(bg='#101621')
        self.running = True
        self.recorder = None
        self.last_time = time.perf_counter()
        self.accumulator = 0.
        self.trail = []
        bar = ttk.Frame(self.window, padding=8)
        bar.pack(fill='x')
        ttk.Label(bar, text='INCHWORM / TACTILE LAB', font=('Segoe UI', 16, 'bold')).pack(side='left', padx=6)
        ttk.Button(bar, text='一時停止 / 再開', command=self.pause).pack(side='left', padx=4)
        ttk.Button(bar, text='リセット', command=self.reset).pack(side='left', padx=4)
        self.record_button = ttk.Button(bar, text='記録開始', command=self.record)
        self.record_button.pack(side='left', padx=4)
        ttk.Button(bar, text='重みを読む', command=self.load_weights).pack(side='left', padx=4)
        self.mode = tk.StringVar(value='両方')
        ttk.Combobox(bar, textvariable=self.mode, values=['両方', '本体ビュー', '触覚グリッド'], state='readonly', width=12).pack(side='left', padx=4)
        self.manual = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='手動目標角', variable=self.manual).pack(side='left', padx=6)
        controls = ttk.Frame(self.window, padding=5)
        controls.pack(fill='x')
        self.gamma = tk.DoubleVar(value=1.)
        self.speed = tk.DoubleVar(value=1.)
        self.display_gain = tk.DoubleVar(value=1.)
        for label, variable, low, high in [('表示ガンマ', self.gamma, .3, 3), ('表示ゲイン', self.display_gain, .5, 3), ('実行倍率', self.speed, .25, 8)]:
            ttk.Label(controls, text=label).pack(side='left', padx=5)
            ttk.Scale(controls, variable=variable, from_=low, to=high, length=110).pack(side='left')
        self.vertical = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text='腹側を縦配置', variable=self.vertical).pack(side='left', padx=5)
        self.gaps = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text='セル間隔', variable=self.gaps).pack(side='left')
        self.scenario = tk.StringVar(value='標準環境')
        selector = ttk.Combobox(controls, textvariable=self.scenario, values=['標準環境', '地面', '餌に接触', '害に接触', '段差に接触'], state='readonly', width=12)
        selector.pack(side='left', padx=8)
        selector.bind('<<ComboboxSelected>>', lambda e: self.reset())
        self.canvas = tk.Canvas(self.window, bg='#101621', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        angles = ttk.Frame(self.window, padding=5)
        angles.pack(fill='x')
        self.sliders = []
        for i in range(6):
            group = ttk.Frame(angles)
            group.pack(side='left', expand=True, fill='x')
            ttk.Label(group, text=f'節{i + 1} pitch' if i < 5 else '頭部 yaw').pack()
            var = tk.DoubleVar(value=0)
            ttk.Scale(group, variable=var, from_=-.9, to=.9).pack(fill='x', padx=8)
            self.sliders.append(var)
        self.status = tk.StringVar(value='準備完了')
        ttk.Label(self.window, textvariable=self.status, padding=6).pack(fill='x')
        self.window.bind('<space>', lambda e: self.pause())
        self.window.bind('<Tab>', self.cycle_view)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.update()

    def cycle_view(self, event=None):
        modes = ['両方', '本体ビュー', '触覚グリッド']
        self.mode.set(modes[(modes.index(self.mode.get()) + 1) % 3])
        return 'break'

    def pause(self):
        self.running = not self.running
        self.accumulator = 0.

    def reset(self):
        if self.recorder:
            self.record()
        config = copy.deepcopy(self.config)
        scenario = self.scenario.get()
        if scenario != '標準環境':
            kinds = {'餌に接触': 'food', '害に接触': 'harm', '段差に接触': 'obstacle'}
            config['environment'] = [] if scenario == '地面' else [{'kind': kinds[scenario], 'x': .245, 'y': 0., 'radius': .06, 'height': .04 if scenario != '害に接触' else .005}]
        self.world = World(config, self.weights)
        self.trail = []
        self.accumulator = 0.

    def record(self):
        if self.recorder:
            self.recorder.close()
            self.recorder = None
            self.record_button.configure(text='記録開始')
        else:
            name = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.csv'
            self.recorder = Recorder(ROOT / 'sessions' / name, self.world)
            self.record_button.configure(text='■ 記録停止')

    def load_weights(self):
        path = filedialog.askopenfilename(initialdir=ROOT, filetypes=[('JSON', '*.json')])
        if path:
            from tkinter import messagebox
            try:
                weights = json.loads(Path(path).read_text())['weights']
                if len(weights) != 8 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in weights):
                    raise ValueError('8 finite weights required')
                self.weights = weights
                self.reset()
            except (ValueError, KeyError, OSError) as error:
                messagebox.showerror('重みを読み込めません', str(error))

    def text(self, x, y, text, size=12, color='#dfe8f3', anchor='nw'):
        self.canvas.create_text(x, y, text=text, font=('Yu Gothic UI', size), fill=color, anchor=anchor)

    def panel(self, x, y, width, height, title, subtitle):
        self.canvas.create_rectangle(x, y, x + width, y + height, fill='#182231', outline='#304156')
        self.text(x + 16, y + 12, title, 14, '#66e0c2')
        self.text(x + 16, y + 39, subtitle, 10, '#9aacc4')

    def draw_scene(self, x, y, width, height):
        self.panel(x, y, width, height, '01 / WORLD · 観察者専用CG', '疑似3D投影 / m単位 / 地面摩擦の簡易モデル / AIへの映像入力なし')
        scale = min(width / .95, (height - 100) / .48)
        center = self.world.x + .19

        def project(px, py, pz):
            return x + width * .38 + (px - center + py * .48) * scale, y + height * .73 + (py * .5 - pz) * scale

        left = math.floor((center - .65) * 10) / 10
        for i in range(18):
            px = left + i * .1
            self.canvas.create_line(*project(px, -.3, 0), *project(px, .3, 0), fill='#2a3b4c')
        for i in range(7):
            py = (i - 3) * .1
            self.canvas.create_line(*project(left, py, 0), *project(left + 1.7, py, 0), fill='#2a3b4c')
        colors = {'food': '#65c99b', 'harm': '#e76c7e', 'obstacle': '#8195ae'}
        for o in self.world.objects:
            if o.get('eaten'):
                continue
            r = o['radius']
            corners = [(o['x'] - r, o['y'] - r), (o['x'] + r, o['y'] - r), (o['x'] + r, o['y'] + r), (o['x'] - r, o['y'] + r)]
            top = [project(px, py, o['height']) for px, py in corners]
            bottom = [project(px, py, 0) for px, py in corners]
            for i in (1, 2):
                j = (i + 1) % 4
                self.canvas.create_polygon(*bottom[i], *bottom[j], *top[j], *top[i], fill='#35445a', outline=colors[o['kind']])
            self.canvas.create_polygon(*[v for pt in top for v in pt], fill=colors[o['kind']], outline='#d5e2ed', stipple='gray50')
            self.text(*project(o['x'], o['y'], o['height'] + .024), o['kind'], 10)
        for px, py in self.trail[-600::3]:
            sx, sy = project(px, py, .002)
            self.canvas.create_oval(sx - 1, sy - 1, sx + 1, sy + 1, fill='#53abbe', outline='')
        for i in range(5):
            a, b = self.world.nodes[i:i + 2]
            self.canvas.create_line(*project(*a), *project(*b), fill='#132d36', width=25, capstyle='round')
            self.canvas.create_line(*project(*a), *project(*b), fill='#63ddc1', width=16, capstyle='round')
            sx, sy = project(*a)
            self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill='#e1fff5', outline='')
            self.text(sx, sy - 22, str(i + 1), 10)
        h = self.world.nodes[-1]
        facing = self.world.heading + self.world.angles[5]
        self.canvas.create_line(*project(*h), *project(h[0] + .035 * math.cos(facing), h[1] + .035 * math.sin(facing), h[2]), fill='#ffd47d', width=6, arrow='last')
        self.text(x + 16, y + height - 30, f'head ({h[0]:+.3f}, {h[1]:+.3f}, {h[2]:+.3f}) m  · 観察・記録のみ', 10)

    def draw_tactile(self, x, y, width, height):
        self.panel(x, y, width, height, '02 / TACTILE · AIが受け取る輝度', '腹側45セル + 頭部9セル / 15 mm / 30 Hz / センサ遅延あり')
        cell = min(25, (width - 50) / (7 if self.vertical.get() else 15), (height - 110) / (15 if self.vertical.get() else 8))
        origin_x, origin_y = x + 20, y + 85
        gap = 2 if self.gaps.get() else 0

        def draw_cell(cx, cy, val):
            level = round(255 * min(1, val * self.display_gain.get()) ** (1 / self.gamma.get()))
            color = f'#{level:02x}{level:02x}{level:02x}'
            self.canvas.create_rectangle(cx, cy, cx + cell - gap, cy + cell - gap, fill=color, outline='')

        for i, val in enumerate(self.world.belly):
            seg, rem = divmod(i, 9)
            row, col = divmod(rem, 3)
            gx, gy = (col, seg * 3 + row) if self.vertical.get() else (seg * 3 + row, col)
            draw_cell(origin_x + gx * cell, origin_y + gy * cell, val)
        hx = origin_x + 4 * cell if self.vertical.get() else origin_x
        hy = origin_y if self.vertical.get() else origin_y + 5 * cell
        self.text(hx, hy - 22, 'HEAD', 10, '#ffd47d')
        for i, val in enumerate(self.world.head):
            draw_cell(hx + (i % 3) * cell, hy + (i // 3) * cell, val)
        self.text(x + 18, y + height - 28, '黒→白 = 低→高輝度（圧力そのものではない）', 10)

    def draw_network(self, x, y, width, height):
        self.panel(x, y, width, height, '03 / LOCAL NCA · 実際の状態・通信・目標角', '共有局所則 / 隣接節のみ通信 / 4チャネル / 初期状態は周期運動の事前設計あり')
        space = (width - 100) / 5
        states = self.world.controller.state
        for i, state in enumerate(states):
            cx = x + 50 + (i + .5) * space
            if i < 4:
                self.canvas.create_line(cx + 32, y + 126, cx + space - 32, y + 126, arrow='both', fill='#5ca5bc', width=2)
                self.text(cx + space / 2, y + 109, '4 ch', 9, '#8cacc4', anchor='center')
            self.text(cx, y + 72, f'節 {i + 1}' + (' / HEAD' if i == 4 else ''), 12, anchor='center')
            self.text(cx, y + 92, f'touch {self.world.controller.features[i][0]:.2f}', 9, '#a6b8cd', anchor='center')
            for k, val in enumerate(state):
                px, py = cx + (k % 2 - .5) * 32, y + 130 + (k // 2) * 37
                color = '#5ce0bd' if val >= 0 else '#ec889c'
                radius = 8 + abs(val) * 5
                self.canvas.create_oval(px - radius, py - radius, px + radius, py + radius, fill=color, outline='')
                self.text(px, py, f'{val:+.1f}', 8, '#10202a', anchor='center')
            self.text(cx, y + 201, f'目標 {math.degrees(self.world.targets[i]):+.1f}°', 11, anchor='center')
            self.text(cx, y + 224, f'実角 {math.degrees(self.world.angles[i]):+.1f}°', 10, '#9aacc4', anchor='center')
        weights = '  '.join(f'{v:+.2f}' for v in self.world.controller.weights)
        self.text(x + 16, y + height - 48, '共有パラメータ [速度, 結合, 振幅, 接触, 粗さ, 記憶, 左右, 前面]  ' + weights, 10)
        label = 'MANUAL：ネットワーク更新停止' if self.manual.get() else f'頭部yaw目標 {math.degrees(self.world.targets[5]):+.1f}°'
        self.text(x + 16, y + height - 25, '各節の4状態: cos位相 / sin位相 / 接触記憶 / 旋回記憶  · ' + label, 10, '#ffd47d')

    def update(self):
        now = time.perf_counter()
        elapsed = min(.25, now - self.last_time)
        self.last_time = now
        if self.running and self.world.hp > 0:
            self.accumulator += elapsed * self.speed.get()
            while self.accumulator >= DT:
                row = self.world.step([v.get() for v in self.sliders] if self.manual.get() else None)
                if self.recorder:
                    self.recorder.write(row)
                self.trail.append((self.world.x, self.world.y))
                self.trail = self.trail[-1800:]
                self.accumulator -= DT
        self.canvas.delete('all')
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w > 100:
            top_h = max(275, h - 320)
            mode = self.mode.get()
            if mode == '両方':
                self.draw_scene(12, 8, w * .63 - 18, top_h)
                self.draw_tactile(w * .63 + 4, 8, w * .37 - 16, top_h)
            elif mode == '本体ビュー':
                self.draw_scene(12, 8, w - 24, top_h)
            else:
                self.draw_tactile(12, 8, w - 24, top_h)
            self.draw_network(12, top_h + 20, w - 24, 290)
        state = '終了 / HP 0' if self.world.hp <= 0 else ('実行中' if self.running else '一時停止')
        self.status.set(f'{state}  |  t={self.world.tick * DT:.2f}s  |  HP {self.world.hp:.1f}  |  {"記録中" if self.recorder else "未記録"}  |  Space: 停止  Tab: 表示切替  |  センサ設定: config.json → 再起動')
        self.window.after(33, self.update)

    def close(self):
        if self.recorder:
            self.recorder.close()
        self.window.destroy()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
    parser.add_argument('--weights')
    args = parser.parse_args()
    weights = json.loads(Path(args.weights).read_text())['weights'] if args.weights else None
    App(read_config(args.config), weights).window.mainloop()
