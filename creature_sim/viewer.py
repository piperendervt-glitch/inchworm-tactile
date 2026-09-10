"""Watch a colony and its stigmergy field on a PC screen.

Two ways in, one screen:

    python -m creature_sim.viewer --listen 47130      # live, from a mirroring server
    python -m creature_sim.viewer --log <session dir> # a session, live or finished

Nothing here sits in the game's path. The live entrance receives copies the
server sends after it has already answered the Body; the log entrance only
reads a file. Either way the viewer can be started, stopped or crash without
the game noticing.

Frames are ``(tick, t, observe, command, field)`` and both entrances produce
the same shape, so the drawing code does not know which one it is looking at.
"""

import argparse
import base64
import json
import math
import socket
import sys
import time
from pathlib import Path

from . import protocol

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'docs' / 'creature-protocol-samples'
DEFAULT_LISTEN = 47130

# Tactile cell colours by contact kind, per docs/creature-protocol.md section 5.
KIND_COLOURS = ('#4a5a66', '#e8eef2', '#ffd24a', '#ff9a4a')
BACKGROUND = '#0d151c'
ARENA_FILL = '#14222c'
ARENA_EDGE = '#4f6b7d'
OBSTACLE = '#39525f'
BODY_FILL = '#2f7d6d'
BODY_DEAD = '#5b4550'
PLAYER = '#ffe196'
PANEL_BG = '#0a1119'
TEXT = '#c9dbe6'
DIM = '#7791a3'


class Frame:
    """One tick's worth of traffic."""

    __slots__ = ('tick', 't', 'observe', 'command', 'field')

    def __init__(self, tick, t, observe, command=None, field=None):
        self.tick = tick
        self.t = t
        self.observe = observe
        self.command = command
        self.field = field

    @property
    def creatures(self):
        return (self.observe or {}).get('creatures') or []

    @property
    def player(self):
        return (self.observe or {}).get('player')

    def command_for(self, creature_id):
        for entry in (self.command or {}).get('creatures') or []:
            if entry.get('id') == creature_id:
                return entry
        return None


def decode_field(message):
    """A ``field`` message into ``(channel, cols, rows, bytes)``."""
    raw = protocol.decode_field_data(message)
    return message['channel'], message['cols'], message['rows'], raw


class LogSource:
    """Reads a session's link.jsonl, seekable and able to follow a live file.

    Line offsets are indexed as the file is read, so jumping back to an
    arbitrary tick is a seek rather than a re-read from the top.
    """

    def __init__(self, path):
        path = Path(path)
        if path.is_dir():
            self.directory = path
            path = path / 'link.jsonl'
        else:
            self.directory = path.parent
        if not path.exists():
            raise FileNotFoundError(f'no link.jsonl at {path}')
        self.path = path
        self.file = path.open('rb')
        self.offsets = []
        self.live = True
        self.meta = self._read_meta()
        self.refresh()

    def _read_meta(self):
        meta = self.directory / 'meta.json'
        if meta.exists():
            try:
                return json.loads(meta.read_text(encoding='utf-8'))
            except ValueError:
                return None
        return None

    @property
    def hello(self):
        return (self.meta or {}).get('hello')

    def refresh(self):
        """Index any lines that have appeared since the last look.

        A partially written final line is left alone; it will be picked up
        once its newline arrives.
        """
        end = self.offsets[-1][1] if self.offsets else 0
        self.file.seek(end)
        added = 0
        while True:
            start = self.file.tell()
            line = self.file.readline()
            if not line:
                break
            if not line.endswith(b'\n'):
                self.file.seek(start)
                break
            if line.strip():
                self.offsets.append((start, self.file.tell()))
                added += 1
        return added

    def __len__(self):
        return len(self.offsets)

    def frame_at(self, index):
        if not 0 <= index < len(self.offsets):
            return None
        start, end = self.offsets[index]
        self.file.seek(start)
        raw = self.file.read(end - start)
        try:
            row = json.loads(raw.decode('utf-8'))
        except ValueError:
            return None
        return Frame(row.get('tick'), row.get('t'), row.get('observe'),
                     row.get('command'), row.get('field'))

    def frames(self):
        for i in range(len(self.offsets)):
            frame = self.frame_at(i)
            if frame is not None:
                yield frame

    def close(self):
        if self.file and not self.file.closed:
            self.file.close()


class MirrorSource:
    """Receives copies the server sends and groups them by tick.

    Messages for one tick arrive as separate datagrams, so a frame is held
    open until something for a later tick shows up. UDP can reorder, so a
    datagram for a tick already released is dropped rather than reviving it.
    """

    def __init__(self, port=DEFAULT_LISTEN, host='0.0.0.0'):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((host, port))
        self.socket.setblocking(False)
        self.port = self.socket.getsockname()[1]
        self.hello = None
        self.session = None
        self.pending = None
        self.released = -1
        self.received = 0
        self.rejected = 0

    def _start(self, message):
        self.pending = Frame(message.get('tick'), message.get('t'), None)

    def feed(self, message):
        """Fold one message in. Returns a completed frame, or None."""
        kind = message.get('type')
        if kind == 'error':
            self.rejected += 1
            return None
        self.received += 1

        session = message.get('session')
        if kind == 'hello':
            self.hello = message
            self.session = session
            self.pending = None
            self.released = -1
            return None
        if kind == 'bye':
            done, self.pending = self.pending, None
            return done
        if session != self.session and self.session is not None:
            # A new run started without us seeing its hello.
            self.session = session
            self.pending = None
            self.released = -1

        tick = message.get('tick')
        # A tick already handed on is water under the bridge. Ignoring it
        # outright matters: letting it through would close the frame that is
        # still open and hand it over a tick early.
        if tick is not None and tick <= self.released:
            return None

        done = None
        if self.pending is not None and tick != self.pending.tick:
            done, self.pending = self.pending, None
            self.released = done.tick if done.tick is not None else self.released
        if self.pending is None:
            self._start(message)

        if kind == 'observe':
            self.pending.observe = message
            self.pending.t = message.get('t')
        elif kind == 'command':
            self.pending.command = message
        elif kind == 'field':
            self.pending.field = message
        return done

    def poll(self, limit=240):
        """Drain the socket. Returns every frame that completed."""
        out = []
        for _ in range(limit):
            try:
                payload, _address = self.socket.recvfrom(65535)
            except BlockingIOError:
                break
            except OSError:
                break
            frame = self.feed(protocol.parse(payload))
            if frame is not None:
                out.append(frame)
        return out

    def flush(self):
        """Release a frame that is complete but has no successor yet."""
        if self.pending is not None and self.pending.observe is not None:
            done, self.pending = self.pending, None
            self.released = done.tick if done.tick is not None else self.released
            return done
        return None

    def close(self):
        self.socket.close()


def load_hello(path=None):
    source = Path(path) if path else SAMPLES / 'hello.json'
    return json.loads(source.read_text(encoding='utf-8'))


def arena_from_hello(hello):
    arena = (hello or {}).get('arena') or {}
    bounds = arena.get('bounds') or [-40.0, -40.0, 40.0, 40.0]
    return [float(v) for v in bounds], arena.get('obstacles') or []


def body_from_hello(hello):
    body = (hello or {}).get('body') or {}
    return (float(body.get('length', 3.0)), float(body.get('radius', 0.8)),
            float(body.get('cellRadius', 0.4)), int(body.get('maxHealth', 120)))


def cell_world_xz(x, z, heading, index, length, radius):
    """Tactile cell position, docs/creature-protocol.md section 5."""
    segment, ring = divmod(index, 8)
    phi = ring * math.pi / 4.0
    axial = (length / 4.0) * (1.0 if segment == 0 else -1.0)
    lateral = radius * math.sin(phi)
    fx, fz = math.sin(heading), math.cos(heading)
    rx, rz = math.sin(heading + math.pi / 2.0), math.cos(heading + math.pi / 2.0)
    return x + fx * axial + rx * lateral, z + fz * axial + rz * lateral


def blend(colour, strength):
    """Fade a colour toward the arena floor. Stands in for opacity, which the
    Canvas does not have for shapes."""
    strength = max(0.0, min(1.0, strength))
    base = (int(ARENA_FILL[1:3], 16), int(ARENA_FILL[3:5], 16), int(ARENA_FILL[5:7], 16))
    top = (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))
    out = [int(b + (t - b) * strength) for b, t in zip(base, top)]
    return '#%02x%02x%02x' % tuple(out)


# One colour per channel, matching the overlay Unity paints on the floor.
CHANNEL_TINT = ((230, 60, 60),      # food trace
                (60, 110, 230),     # path trace
                (235, 145, 45),     # damage trace
                (165, 80, 220))     # death trace
CHANNEL_NAME = ('food trace', 'path trace', 'damage trace', 'death trace')
# What the f key cycles through. Four traces at once turn the floor to mud, so
# the warnings can be looked at on their own.
CHANNEL_VIEWS = ((0, 1, 2, 3), (0, 1), (2, 3))
VIEW_NAME = ('all traces', 'food + path', 'damage + death')


class FieldImage:
    """Every channel as one picture, written in bulk rather than per cell."""

    def __init__(self, cols=64, rows=64):
        self.cols = cols
        self.rows = rows
        self.channels = {}
        self.dirty = False

    def update(self, message):
        channel, cols, rows, raw = decode_field(message)
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.channels.clear()
        self.channels[channel] = raw
        self.dirty = True

    # A faint trace is the interesting part, so the ramp lifts small values
    # hard. Without it a fresh path trace is indistinguishable from the floor.
    RAMP = tuple(int(40 + 215 * (v / 255.0) ** 0.45) if v else 0 for v in range(256))

    def rows_of_colours(self, view=None):
        """Row strings for PhotoImage.put, one colour per channel.

        Where several traces mark the same cell the strongest one is shown,
        so a spot that is both well travelled and dangerous reads as dangerous
        rather than as a muddy average of the two.
        """
        wanted = CHANNEL_VIEWS[0] if view is None else view
        planes = [(c, self.channels[c]) for c in wanted if c in self.channels]
        ramp = self.RAMP
        out = []
        # Field row 0 is the smallest Z, the far edge, but the first row
        # string becomes the top of the picture and the arena is drawn with
        # the largest Z at the top. So the rows go out last to first.
        for row in range(self.rows - 1, -1, -1):
            base = row * self.cols
            cells = []
            for col in range(self.cols):
                best = 0
                tint = None
                for channel, plane in planes:
                    value = ramp[plane[base + col]]
                    if value > best:
                        best, tint = value, CHANNEL_TINT[channel % len(CHANNEL_TINT)]
                if tint is None:
                    cells.append(ARENA_FILL)
                else:
                    cells.append('#%02x%02x%02x' % (tint[0] * best // 255,
                                                    tint[1] * best // 255,
                                                    tint[2] * best // 255))
            out.append('{' + ' '.join(cells) + '}')
        return ' '.join(out)


class ViewerApp:
    """tkinter screen. Built on the same idea as creature_sim/ecoli/gui.py."""

    MAP = 640
    PANEL = 300
    HEIGHT = 680
    # Extra height for the learning score, only when there is one to show.
    SCORE_ROOM = 190

    def __init__(self, source, hello=None, speed=1, title='Creature Viewer',
                 scores=None, status=None, learner=None):
        import tkinter as tk

        self.tk = tk
        self.source = source
        self.replay = isinstance(source, LogSource)
        self.hello = hello or getattr(source, 'hello', None) or load_hello()
        self.bounds, self.obstacles = arena_from_hello(self.hello)
        self.length, self.radius, self.cell_radius, self.max_health = body_from_hello(self.hello)
        self.field_image = FieldImage()
        self.frame = None
        self.index = 0
        self.speed = speed
        self.paused = False
        self.accumulator = 0.0
        self.last = time.perf_counter()
        self.seeking = False
        self.frames_seen = 0
        self.view = 0

        # A learning run's scores and progress, when the autoloop started us.
        self.scores_path = Path(scores) if scores else None
        self.status_path = Path(status) if status else None
        self.learner_path = Path(learner) if learner else None
        self.scores = {}
        self.run_status = {}
        self.learner_status = {}
        self.scores_read = 0.0
        if self.scores_path is not None:
            self.HEIGHT = ViewerApp.HEIGHT + self.SCORE_ROOM

        self.window = tk.Tk()
        self.window.title(title)
        self.window.configure(bg=BACKGROUND)
        self.canvas = tk.Canvas(self.window, width=self.MAP + self.PANEL,
                                height=self.HEIGHT, bg=BACKGROUND, highlightthickness=0)
        self.canvas.pack(side='top')
        self.photo = tk.PhotoImage(width=self.field_image.cols, height=self.field_image.rows)

        self.status = tk.StringVar(value='')
        bar = tk.Frame(self.window, bg=BACKGROUND)
        bar.pack(side='top', fill='x')
        tk.Label(bar, textvariable=self.status, bg=BACKGROUND, fg=DIM,
                 anchor='w').pack(side='left', padx=8)
        if self.replay:
            self.scale = tk.Scale(bar, from_=0, to=1, orient='horizontal', length=460,
                                  bg=BACKGROUND, fg=TEXT, highlightthickness=0,
                                  troughcolor=PANEL_BG, showvalue=False,
                                  command=self.on_scale)
            self.scale.pack(side='right', padx=8)
        else:
            self.scale = None

        self.window.bind('<space>', self.on_pause)
        self.window.bind('<Right>', self.on_step)
        self.window.bind('<Left>', self.on_back)
        for key in '1234567890':
            self.window.bind(key, self.on_speed)
        self.window.bind('f', self.on_view)
        self.window.bind('F', self.on_view)
        self.window.bind('<Escape>', lambda _e: self.window.destroy())
        self.window.protocol('WM_DELETE_WINDOW', self.window.destroy)
        self.window.after(33, self.update)

    # -- input ----------------------------------------------------------

    def on_pause(self, _event=None):
        self.paused = not self.paused

    def on_step(self, _event=None):
        if self.replay:
            self.paused = True
            self.seek(self.index + 1)

    def on_back(self, _event=None):
        if self.replay:
            self.paused = True
            self.seek(self.index - 1)

    def on_speed(self, event):
        # 1..9 select that speed, 0 selects the top speed of 20.
        self.speed = 20 if event.char == '0' else int(event.char)

    def on_view(self, _event=None):
        self.view = (self.view + 1) % len(CHANNEL_VIEWS)
        self.field_image.dirty = True

    def on_scale(self, value):
        # Tk may run this after the call that moved the slider has returned, so
        # a flag set around scale.set() cannot tell our own move from a drag.
        # Comparing against where we already are can.
        if not self.replay:
            return
        index = int(float(value))
        if index == self.index:
            return
        self.seek(index)
        self.paused = True

    def seek(self, index):
        index = max(0, min(len(self.source) - 1, index))
        frame = self.source.frame_at(index)
        if frame is None:
            return
        self.index = index
        self.frame = frame
        # The field is only sent twice a second, so the picture for an
        # arbitrary tick is the most recent one at or before it.
        self.field_image.channels.clear()
        for i in range(max(0, index - 60), index + 1):
            other = self.source.frame_at(i)
            if other is not None and other.field:
                self.field_image.update(other.field)
        self.seeking = True
        if self.scale is not None:
            self.scale.set(index)
        self.seeking = False

    # -- clock ----------------------------------------------------------

    def update(self):
        now = time.perf_counter()
        elapsed = min(0.25, now - self.last)
        self.last = now

        if self.replay:
            added = self.source.refresh()
            if self.scale is not None and (added or self.scale.cget('to') != max(1, len(self.source) - 1)):
                self.seeking = True
                self.scale.configure(to=max(1, len(self.source) - 1))
                self.seeking = False
            if not self.paused and len(self.source):
                self.accumulator += elapsed * 30.0 * self.speed
                advance = int(self.accumulator)
                if advance:
                    self.accumulator -= advance
                    self.seek(self.index + advance)
        else:
            frames = self.source.poll()
            if not frames:
                late = self.source.flush()
                if late is not None:
                    frames = [late]
            for frame in frames:
                self.frames_seen += 1
                if self.paused:
                    break
                self.frame = frame
                if frame.field:
                    self.field_image.update(frame.field)
            if self.source.hello is not None and self.source.hello is not self.hello:
                self.hello = self.source.hello
                self.bounds, self.obstacles = arena_from_hello(self.hello)
                (self.length, self.radius,
                 self.cell_radius, self.max_health) = body_from_hello(self.hello)

        self.refresh_scores(now)
        self.render()
        self.window.after(33, self.update)

    def refresh_scores(self, now):
        """Re-read the run's score and status files every two seconds."""
        if self.scores_path is None or now - self.scores_read < 2.0:
            return
        self.scores_read = now
        from . import scoreboard
        self.scores = scoreboard.load(self.scores_path)
        self.run_status = scoreboard.load(self.status_path) if self.status_path else {}
        self.learner_status = scoreboard.load(self.learner_path) if self.learner_path else {}

    # -- drawing --------------------------------------------------------

    def layout(self):
        """Pixels per field cell, and the arena rectangle they add up to.

        The field picture can only be enlarged by whole numbers, so the arena
        is drawn at exactly that size. Fitting the arena first and enlarging
        afterwards would leave the field short of the right and bottom edges
        by the rounding.
        """
        cols = max(1, self.field_image.cols)
        rows = max(1, self.field_image.rows)
        min_x, min_z, max_x, max_z = self.bounds
        room = self.MAP - 24
        span = max(max_x - min_x, max_z - min_z)
        zx = max(1, int(room * ((max_x - min_x) / span) / cols))
        zy = max(1, int(room * ((max_z - min_z) / span) / rows))
        return zx, zy, cols * zx, rows * zy

    def to_screen(self, x, z):
        min_x, min_z, max_x, max_z = self.bounds
        _zx, _zy, width, height = self.layout()
        cx = 12 + (x - min_x) / (max_x - min_x) * width
        # Screen y grows downward while world Z grows away, so Z is flipped.
        cy = 12 + (max_z - z) / (max_z - min_z) * height
        return cx, cy

    def render(self):
        c = self.canvas
        c.delete('all')
        self.draw_field()
        self.draw_arena()
        if self.frame is not None:
            self.draw_prey()
            self.draw_creatures()
            self.draw_player()
        self.draw_panel()
        self.status.set(self.status_text())

    def draw_field(self):
        if not self.field_image.channels:
            return
        if self.field_image.dirty:
            if (self.photo.width() != self.field_image.cols
                    or self.photo.height() != self.field_image.rows):
                self.photo = self.tk.PhotoImage(width=self.field_image.cols,
                                                height=self.field_image.rows)
            self.photo.put(self.field_image.rows_of_colours(CHANNEL_VIEWS[self.view]), to=(0, 0))
            self.field_image.dirty = False
        zx, zy, _w, _h = self.layout()
        left, top = self.to_screen(self.bounds[0], self.bounds[3])
        # Held on the instance: a PhotoImage the canvas is showing must not be
        # collected, and zoom() makes a new one each time.
        self.scaled = self.photo.zoom(zx, zy)
        self.canvas.create_image(left, top, image=self.scaled, anchor='nw')

    def draw_arena(self):
        min_x, min_z, max_x, max_z = self.bounds
        left, top = self.to_screen(min_x, max_z)
        right, bottom = self.to_screen(max_x, min_z)
        if not self.field_image.channels:
            self.canvas.create_rectangle(left, top, right, bottom,
                                         fill=ARENA_FILL, outline=ARENA_EDGE)
        else:
            self.canvas.create_rectangle(left, top, right, bottom, outline=ARENA_EDGE)
        for obstacle in self.obstacles:
            cx, cz = obstacle.get('center', (0, 0))
            if obstacle.get('shape') == 'circle':
                r = float(obstacle.get('radius', 1.0))
                a, b = self.to_screen(cx - r, cz + r)
                d, e = self.to_screen(cx + r, cz - r)
                self.canvas.create_oval(a, b, d, e, fill=OBSTACLE, outline=ARENA_EDGE)
            else:
                sx, sz = obstacle.get('size', (1.0, 1.0))
                a, b = self.to_screen(cx - sx / 2, cz + sz / 2)
                d, e = self.to_screen(cx + sx / 2, cz - sz / 2)
                self.canvas.create_rectangle(a, b, d, e, fill=OBSTACLE, outline=ARENA_EDGE)

    def draw_creatures(self):
        min_x, min_z, max_x, max_z = self.bounds
        _zx, _zy, width, _height = self.layout()
        scale = width / (max_x - min_x)
        for creature in self.frame.creatures:
            if creature.get('state') == 'dead':
                continue
            pos = creature.get('pos') or [0, 0, 0]
            x, z = float(pos[0]), float(pos[2])
            heading = float(creature.get('heading', 0.0))
            r = self.radius * scale
            cx, cy = self.to_screen(x, z)
            entry = self.frame.command_for(creature.get('id')) or {}
            fill = BODY_DEAD if entry.get('starved') else BODY_FILL
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline='')
            # Nose, so the heading is readable at a glance.
            nx, nz = x + math.sin(heading) * self.length / 2, z + math.cos(heading) * self.length / 2
            ax, ay = self.to_screen(nx, nz)
            self.canvas.create_line(cx, cy, ax, ay, fill='#e6f2f7', width=2, arrow='last')

            cr = max(2.0, self.cell_radius * scale)
            for i, cell in enumerate(creature.get('cells') or []):
                wx, wz = cell_world_xz(x, z, heading, i, self.length, self.radius)
                sx, sy = self.to_screen(wx, wz)
                kind = int(cell.get('k', 0))
                p = float(cell.get('p', 0.0))
                colour = blend(KIND_COLOURS[kind], 0.25 + 0.75 * p) if kind else KIND_COLOURS[0]
                self.canvas.create_oval(sx - cr, sy - cr, sx + cr, sy + cr,
                                        fill=colour, outline='')
            self.canvas.create_text(cx, cy - r - 8, text=str(creature.get('id')),
                                    fill=DIM, font=('Consolas', 9))

    def draw_prey(self):
        """Food other than the pilot: a triangle for a mech, a slab for a wreck, with an HP bar."""
        for item in (self.frame.observe or {}).get('prey') or ():
            pos = item.get('pos') or [0, 0, 0]
            cx, cy = self.to_screen(float(pos[0]), float(pos[2]))
            if item.get('kind') == 'wreck':
                self.canvas.create_rectangle(cx - 9, cy - 6, cx + 9, cy + 6,
                                             fill='#5a5048', outline='#b8a898')
            else:
                self.canvas.create_polygon(cx, cy - 9, cx - 8, cy + 6, cx + 8, cy + 6,
                                           fill='#b04ca0', outline='#f0c8e8')
            max_hp = max(1, int(item.get('maxHp') or 1))
            share = max(0.0, min(1.0, float(item.get('hp') or 0) / max_hp))
            self.canvas.create_rectangle(cx - 10, cy + 9, cx + 10, cy + 12, fill='#302828', outline='')
            self.canvas.create_rectangle(cx - 10, cy + 9, cx - 10 + 20 * share, cy + 12,
                                         fill='#8fd06a', outline='')

    def draw_player(self):
        player = self.frame.player
        if not player:
            return
        pos = player.get('pos') or [0, 0, 0]
        cx, cy = self.to_screen(float(pos[0]), float(pos[2]))
        self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill=PLAYER,
                                outline='#fff6d1', width=2)
        self.canvas.create_text(cx, cy - 16, text='PLAYER', fill=PLAYER,
                                font=('Consolas', 8))

    def draw_panel(self):
        c = self.canvas
        left = self.MAP
        c.create_rectangle(left, 0, left + self.PANEL, self.HEIGHT, fill=PANEL_BG, outline='')
        x = left + 16
        y = 18
        mode = 'REPLAY' if self.replay else 'LIVE'
        c.create_text(x, y, text=f'{mode}{"  PAUSED" if self.paused else ""}',
                      anchor='w', fill=TEXT, font=('Consolas', 12, 'bold'))
        y += 24
        if self.frame is not None:
            c.create_text(x, y, text=f'tick {self.frame.tick}   t {self.frame.t or 0:.2f}s',
                          anchor='w', fill=DIM, font=('Consolas', 10))
        y += 18
        if self.replay:
            c.create_text(x, y, text=f'{self.index + 1} / {len(self.source)}   x{self.speed}',
                          anchor='w', fill=DIM, font=('Consolas', 10))
        else:
            c.create_text(x, y, text=f'frames {self.frames_seen}   port {self.source.port}',
                          anchor='w', fill=DIM, font=('Consolas', 10))
        y += 26

        c.create_text(x, y, text='contact', anchor='w', fill=TEXT, font=('Consolas', 10, 'bold'))
        y += 16
        for kind, name in enumerate(('none', 'wall', 'player', 'creature')):
            c.create_oval(x, y - 5, x + 10, y + 5, fill=KIND_COLOURS[kind], outline='')
            c.create_text(x + 18, y, text=name, anchor='w', fill=DIM, font=('Consolas', 9))
            y += 15
        y += 6
        c.create_text(x, y, text='field', anchor='w', fill=TEXT, font=('Consolas', 10, 'bold'))
        c.create_text(x + 60, y, text='f: ' + VIEW_NAME[self.view], anchor='w',
                      fill='#4c6373', font=('Consolas', 9))
        y += 16
        shown = CHANNEL_VIEWS[self.view]
        for channel, name in enumerate(CHANNEL_NAME):
            tint = CHANNEL_TINT[channel]
            on = channel in shown
            c.create_rectangle(x, y - 5, x + 10, y + 5,
                               fill='#%02x%02x%02x' % tint if on else PANEL_BG,
                               outline='#3a4a57' if not on else '')
            c.create_text(x + 18, y, text=name, anchor='w',
                          fill=DIM if on else '#3a4a57', font=('Consolas', 9))
            y += 15
        y += 10

        bar = x + 168
        c.create_text(x, y, text=' id mode    state', anchor='w', fill=TEXT,
                      font=('Consolas', 9, 'bold'))
        c.create_text(bar, y, text='energy', anchor='w', fill=TEXT,
                      font=('Consolas', 9, 'bold'))
        y += 16
        for creature in (self.frame.creatures if self.frame else []):
            entry = self.frame.command_for(creature.get('id')) or {}
            energy = float(entry.get('energy', 0.0))
            state = creature.get('state', '')
            if entry.get('starved'):
                state = 'starved'
            elif creature.get('eating'):
                state = 'eating'
            colour = PLAYER if creature.get('eating') else DIM
            c.create_text(x, y, anchor='w', fill=colour, font=('Consolas', 9),
                          text=f"{creature.get('id'):>3} {entry.get('mode', '-'):<8}{state}")
            c.create_rectangle(bar, y - 4, bar + 60, y + 4, outline='#25323d')
            c.create_rectangle(bar, y - 4, bar + 60 * max(0.0, min(1.0, energy)), y + 4,
                               fill='#6be6ca', outline='')
            c.create_text(bar + 66, y, text=f'{energy:.2f}', anchor='w', fill=DIM,
                          font=('Consolas', 8))
            y += 15

        if self.scores_path is not None:
            self.draw_scores(x, ViewerApp.HEIGHT)

        keys = ('space pause  arrows step  1-9,0 speed  f traces' if self.replay
                else 'space hold  f traces')
        c.create_text(x, self.HEIGHT - 18, text=keys, anchor='w', fill='#4c6373',
                      font=('Consolas', 8))

    def draw_scores(self, x, y):
        """Start, latest and best benchmark score, a bar per generation, and what is running."""
        from . import scoreboard
        c = self.canvas
        jp = ('Yu Gothic UI', 10)
        c.create_line(x, y - 14, x + self.PANEL - 32, y - 14, fill='#25323d')
        c.create_text(x, y, text='学習スコア（固定ベンチマーク）', anchor='w', fill=TEXT,
                      font=('Yu Gothic UI', 10, 'bold'))
        y += 20
        for line in scoreboard.summary_lines(self.scores):
            c.create_text(x, y, text=line, anchor='w', fill=DIM, font=jp)
            y += 17

        values = [h.get('fitness', 0.0) for h in self.scores.get('history', [])][-24:]
        if values:
            top = max(values)
            low = min(0.0, min(values))
            span = max(1e-9, top - low)
            width = self.PANEL - 32
            step = width / max(len(values), 1)
            base = y + 34
            for i, value in enumerate(values):
                height = 30 * (value - low) / span
                last = i == len(values) - 1
                c.create_rectangle(x + i * step + 1, base - height, x + (i + 1) * step - 1, base,
                                   fill='#8fd06a' if last else '#4f7a45', outline='')
            y = base + 12

        for label, status in (('出撃', self.run_status), ('学習', self.learner_status)):
            if status:
                text = f'{label}: {status.get("state", "")} {status.get("detail", "")}'.strip()
                c.create_text(x, y, text=text[:40], anchor='w', fill=DIM, font=jp)
                y += 16

    def status_text(self):
        if self.frame is None:
            return 'waiting for data...'
        alive = sum(1 for c in self.frame.creatures if c.get('state') != 'dead')
        eating = sum(1 for c in self.frame.creatures if c.get('eating'))
        return f'{alive} alive   {eating} eating'

    def run(self):
        self.window.mainloop()
        if hasattr(self.source, 'close'):
            self.source.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.viewer',
                                description='Watch a colony and its stigmergy field')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--listen', type=int, nargs='?', const=DEFAULT_LISTEN,
                       help='receive mirrored traffic on this UDP port')
    group.add_argument('--log', help='a session directory or link.jsonl to replay')
    p.add_argument('--hello', help='hello.json for the arena when listening')
    p.add_argument('--speed', type=int, default=1, help='replay speed, 1 to 20')
    p.add_argument('--scores', help="a learning run's scores.json, shown under the panel")
    p.add_argument('--status', help="the run's status.json (what is flying)")
    p.add_argument('--learner', help="the run's learner.json (what is training)")
    a = p.parse_args(argv)

    if a.log:
        source = LogSource(a.log)
        hello = load_hello(a.hello) if a.hello else source.hello
        title = f'Creature Viewer - {Path(a.log).name}'
    else:
        source = MirrorSource(a.listen)
        hello = load_hello(a.hello)
        title = f'Creature Viewer - live :{source.port}'
        print(f'[viewer] listening on udp {source.port}', flush=True)

    ViewerApp(source, hello=hello, speed=max(1, min(20, a.speed)), title=title,
              scores=a.scores, status=a.status, learner=a.learner).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
