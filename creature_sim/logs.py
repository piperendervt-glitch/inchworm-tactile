"""Turn a session's raw link log into the tables the learning phase reads.

The server writes ``link.jsonl``: one line per tick holding the whole exchange.
That is the right shape for a viewer and the wrong shape for training, which
wants columns it can scan. This converts one into the other, as described in
mech-arena-quest/docs/learning-monster-design.md section 5:

    meta.json         the arena, the body, the brain that played
    creature-<id>.csv one row per tick per individual
    player.csv        the pilot's path, for scoring only
    events.csv        spawns, hits, meals and deaths

Run it over a session directory:

    python -m creature_sim.logs sessions/creature/<stamp>
"""

import argparse
import csv
import json
import sys
from pathlib import Path

SCHEMA = 1

CREATURE_COLUMNS = ('tick', 't', 'x', 'z', 'heading', 'speed', 'hp', 'state',
                    'reason', 'eating', 'mode', 'turn', 'deposit', 'energy',
                    'starved', 'hit')
PLAYER_COLUMNS = ('tick', 't', 'x', 'z', 'heading', 'hp', 'guard')
EVENT_COLUMNS = ('tick', 't', 'type', 'creature', 'value')


def read_rows(path):
    """Every tick of a link log, skipping anything unreadable."""
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


class Session:
    """One recorded session, ready to be converted or replayed."""

    def __init__(self, directory):
        directory = Path(directory)
        if directory.is_file():
            directory = directory.parent
        self.directory = directory
        self.link = directory / 'link.jsonl'
        if not self.link.exists():
            raise FileNotFoundError(f'no link.jsonl in {directory}')
        meta = directory / 'meta.json'
        self.meta = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}

    @property
    def hello(self):
        return self.meta.get('hello') or {}

    @property
    def arena(self):
        return self.hello.get('arena') or {}

    @property
    def bounds(self):
        raw = self.arena.get('bounds') or [-40.0, -40.0, 40.0, 40.0]
        return [float(v) for v in raw]

    @property
    def obstacles(self):
        return self.arena.get('obstacles') or []

    @property
    def body(self):
        return self.hello.get('body') or {}

    @property
    def brain(self):
        return (self.meta.get('welcome') or {}).get('brain') or {}

    def rows(self):
        return read_rows(self.link)

    def _tracks(self):
        """Read the log once for both the pilot's path and the rest of the world."""
        cached = getattr(self, '_cached_tracks', None)
        if cached is not None:
            return cached
        player_out, world_out = [], []
        for row in self.rows():
            observe = row.get('observe') or {}
            if not observe:
                continue
            t = float(row.get('t') or 0.0)
            player = observe.get('player')
            # A ghost pilot is not reported at all; the world still is.
            if player:
                pos = player.get('pos') or [0.0, 0.0, 0.0]
                player_out.append((t, float(pos[0]), float(pos[2]),
                                   float(player.get('heading') or 0.0),
                                   int(player.get('hp') or 0)))
            has_sounds = 'sounds' in observe
            world_out.append((t, list(observe.get('prey') or []),
                              list(observe.get('sounds') or []) if has_sounds else None))
        self._cached_tracks = (player_out, world_out)
        return self._cached_tracks

    def player_track(self):
        """The pilot's path as ``(t, x, z, heading, hp)``, in tick order."""
        return self._tracks()[0]

    def world_track(self):
        """Other food and sounds as ``(t, prey, sounds)``, aligned with the pilot's track.

        ``sounds`` is None for a tick recorded before the Body had ears.
        """
        return self._tracks()[1]


def convert(directory, out_dir=None):
    """Write the training tables next to the log. Returns the paths written."""
    session = Session(directory)
    out_dir = Path(out_dir) if out_dir else session.directory
    out_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    files = {}
    writers = {}
    player_rows = []
    events = []
    previous_state = {}
    try:
        for row in session.rows():
            observe = row.get('observe') or {}
            command = row.get('command') or {}
            tick = row.get('tick')
            t = row.get('t')
            orders = {c.get('id'): c for c in (command.get('creatures') or [])}

            player = observe.get('player')
            if player:
                pos = player.get('pos') or [0.0, 0.0, 0.0]
                player_rows.append(dict(tick=tick, t=t, x=pos[0], z=pos[2],
                                        heading=player.get('heading'),
                                        hp=player.get('hp'),
                                        guard=int(bool(player.get('guard')))))

            for creature in observe.get('creatures') or []:
                cid = creature.get('id')
                if cid not in writers:
                    path = out_dir / f'creature-{cid}.csv'
                    files[cid] = path.open('w', newline='', encoding='utf-8')
                    writers[cid] = csv.DictWriter(files[cid], CREATURE_COLUMNS)
                    writers[cid].writeheader()
                    written[f'creature-{cid}'] = path

                pos = creature.get('pos') or [0.0, 0.0, 0.0]
                cells = creature.get('cells') or []
                hit = max((float(c.get('h') or 0.0) for c in cells), default=0.0)
                order = orders.get(cid) or {}
                state = creature.get('state', 'alive')
                writers[cid].writerow(dict(
                    tick=tick, t=t, x=pos[0], z=pos[2],
                    heading=creature.get('heading'), speed=creature.get('speed'),
                    hp=creature.get('hp'), state=state,
                    reason=creature.get('reason') or '',
                    eating=int(bool(creature.get('eating'))),
                    mode=order.get('mode', ''), turn=order.get('turn', ''),
                    deposit=order.get('deposit', ''), energy=order.get('energy', ''),
                    starved=int(bool(order.get('starved'))), hit=round(hit, 4)))

                if state == 'spawned':
                    events.append(dict(tick=tick, t=t, type='spawn', creature=cid, value=''))
                if hit > 0:
                    events.append(dict(tick=tick, t=t, type='hit', creature=cid, value=round(hit, 4)))
                if creature.get('eating') and not previous_state.get(cid, {}).get('eating'):
                    events.append(dict(tick=tick, t=t, type='eat', creature=cid, value=''))
                if state == 'dead':
                    events.append(dict(tick=tick, t=t, type='die', creature=cid,
                                       value=creature.get('reason') or ''))
                previous_state[cid] = dict(eating=bool(creature.get('eating')))
    finally:
        for f in files.values():
            f.close()

    player_path = out_dir / 'player.csv'
    with player_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, PLAYER_COLUMNS)
        writer.writeheader()
        writer.writerows(player_rows)
    written['player'] = player_path

    events_path = out_dir / 'events.csv'
    with events_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, EVENT_COLUMNS)
        writer.writeheader()
        writer.writerows(events)
    written['events'] = events_path

    meta_path = out_dir / 'meta.json'
    if meta_path != session.directory / 'meta.json' or True:
        payload = dict(schema=SCHEMA, source=str(session.directory),
                       arena=session.arena, body=session.body, brain=session.brain,
                       ticks=len(player_rows), creatures=sorted(writers),
                       events=len(events))
        # Keep the original hello alongside, so a replay has everything.
        payload['hello'] = session.hello
        meta_path.write_text(json.dumps(payload, indent=1) + '\n', encoding='utf-8')
    written['meta'] = meta_path
    return written


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.logs',
                                description='Convert a session log into training tables')
    p.add_argument('session', help='a session directory or its link.jsonl')
    p.add_argument('--out', help='where to write (default: alongside the log)')
    a = p.parse_args(argv)
    written = convert(a.session, a.out)
    for name, path in sorted(written.items()):
        print(f'{name:14s} {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
