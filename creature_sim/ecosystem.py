"""Selection without learning: both species living, dying and dividing in a
recorded arena, as fast as Python can run it, with no Unity and no training.

    python -m creature_sim.ecosystem --session sessions/creature/<stamp> \\
        --brain brains/ecoli-gen-006.json --seconds 600 --runs 3

Each run starts from the lineage file (the survivors of the run before), so
what changes across runs is only what survived. The brain is fixed. A run is
written like a real session (link.jsonl, meta.json, summary.json), so the
viewer can play it back:

    python -m creature_sim.viewer --log sessions/ecosystem/<stamp>

What is simulated: the arena and its current, the pilot on the recorded path
(or no pilot, with --no-pilot), the recorded mechs and wrecks, the plankton
wrecks shed, E. coli biting the pilot, mechs, wrecks and jellies, jellies
grazing, starvation, division into a capped pool. What is not: shooting. A
mech here is food that does not shoot back, and nothing dies of a bullet.
"""

import argparse
import datetime
import json
import math
import sys
from pathlib import Path

from . import genome as genes, lineage, protocol
from .ecoli.brain import EcoliBrain
from .field import PLANKTON
from .jelly.colony import JellyColony
from .replay import CELLS, KIND_NONE, KIND_OTHER, KIND_PREY, KIND_WALL, PREY_RADIUS, Body, ReplayWorld

ROOT = Path(__file__).resolve().parents[1]
FIRST_BITE, BITE_INTERVAL = 0.5, 1.0
JELLY_BITES = 2
JELLY_MAX_HP = 20
ECOLI_BITES = 10


class Creature(Body):
    __slots__ = ('species', 'hp', 'bite_left', 'bite', 'meal', 'parent', 'born', 'reason')

    def __init__(self, creature_id, species, x, z, heading, parent=-1):
        super().__init__(creature_id, x, z, heading)
        self.species = species
        self.hp = JELLY_MAX_HP if species == 'jelly' else 120
        self.bite_left = FIRST_BITE
        self.bite = None            # what was bitten this tick
        self.meal = None            # (kind, target) under the mouth
        self.parent = parent
        self.born = True
        self.reason = None


class Ecosystem(ReplayWorld):
    """Two species in a recorded arena, selected by what they can eat."""

    def __init__(self, session, brain=None, seed=0, hz=30, ecoli=6, jelly=6,
                 ecoli_capacity=16, jelly_capacity=16, pilot=True, lineage_path=None,
                 settings=None, jelly_settings=None):
        self.pilot = pilot
        self.saved = lineage.load(lineage_path) if lineage_path else dict(ecoli=[], jelly=[])
        self.jelly_settings = dict(jelly_settings or {})
        # ReplayWorld places `count` E. coli; we place both kinds ourselves.
        super().__init__(session, brain=brain, count=0, seed=seed, hz=hz, settings=settings)
        self.colony.founders = [dict(f['genome']) for f in self.saved['ecoli']]
        jelly_body = self.session.hello.get('jelly') or {}
        self.jellies = JellyColony(field=self.field, seed=seed,
                                   settings=dict(run_speed=float(jelly_body.get('runSpeed', 3.0)),
                                                 **self.jelly_settings),
                                   founders=self.saved['jelly'])
        self.jelly_speed = float(jelly_body.get('runSpeed', 3.0))
        self.capacity = dict(ecoli=ecoli_capacity, jelly=jelly_capacity)
        self.next_id = dict(ecoli=0, jelly=ecoli_capacity)
        self.bodies = []
        self.births = dict(ecoli=0, jelly=0)
        self.deaths = dict(ecoli=dict(starved=0, eaten=0), jelly=dict(starved=0, eaten=0))
        self.bites = {}
        self.rows = []
        for _ in range(ecoli):
            self.place('ecoli')
        for _ in range(jelly):
            self.place('jelly')

    # -- population -----------------------------------------------------

    def alive(self, species=None):
        return [b for b in self.bodies if b.alive and (species is None or b.species == species)]

    def _free_id(self, species):
        low, high = (0, self.capacity['ecoli']) if species == 'ecoli' else \
            (self.capacity['ecoli'], self.capacity['ecoli'] + self.capacity['jelly'])
        used = {b.id for b in self.bodies if b.alive}
        for i in range(low, high):
            if i not in used:
                return i
        return None

    def place(self, species, near=None, parent=-1):
        creature_id = self._free_id(species)
        if creature_id is None:
            return None
        if near is None:
            stand_in = self._spawn(creature_id)
            body = Creature(creature_id, species, stand_in.x, stand_in.z, stand_in.heading, parent)
        else:
            body = None
            for k in range(8):
                a = near.heading + (k * 45 + 22.5) * math.pi / 180
                x = near.x + math.sin(a) * self.length * 0.75
                z = near.z + math.cos(a) * self.length * 0.75
                if not self._blocked(x, z, self.radius):
                    body = Creature(creature_id, species, x, z, a, parent)
                    break
            if body is None:
                return None
            self.births[species] += 1
        # A dead body with this id may still sit in the list; drop it.
        self.bodies = [b for b in self.bodies if b.alive or b.id != creature_id]
        self.bodies.append(body)
        return body

    # -- sensing --------------------------------------------------------

    def sense(self, body, others):
        touched = False
        eating = False
        best_meal = None
        best_depth = -1.0
        r = self.cell_radius
        for i, cell in enumerate(body.cells):
            cx, cz = self.cell_world_xz(body, i)
            depths = {}
            wall = max(self._wall_depth(cx, cz, r), self._obstacle_depth(cx, cz, r))
            if wall > 0.0:
                depths[KIND_WALL] = wall
            if body.species == 'ecoli':
                if self.pilot:
                    d = r + PREY_RADIUS['player'] - math.hypot(cx - self.player[0], cz - self.player[1])
                    if d > 0.0:
                        depths[KIND_PREY] = d
                        if d > best_depth:
                            best_depth, best_meal = d, ('player', None)
                for item in self.prey:
                    pos = item.get('pos')
                    if not pos:
                        continue
                    d = r + PREY_RADIUS.get(item.get('kind'), 1.0) - math.hypot(cx - float(pos[0]), cz - float(pos[2]))
                    if d > 0.0:
                        depths[KIND_PREY] = max(depths.get(KIND_PREY, 0.0), d)
                        if d > best_depth:
                            best_depth, best_meal = d, (item.get('kind', 'mech'), None)
            for other in others:
                if other is body or not other.alive:
                    continue
                gap = r + self.radius - math.hypot(cx - other.x, cz - other.z)
                if gap <= 0.0:
                    continue
                if body.species == 'ecoli' and other.species == 'jelly':
                    depths[KIND_PREY] = max(depths.get(KIND_PREY, 0.0), gap)
                    if gap > best_depth:
                        best_depth, best_meal = gap, ('jelly', other)
                else:
                    depths[KIND_OTHER] = max(depths.get(KIND_OTHER, 0.0), gap)
            if depths:
                kind = next(k for k in (KIND_PREY, KIND_OTHER, KIND_WALL) if k in depths)
                depth = max(depths.values())
            else:
                kind, depth = KIND_NONE, 0.0
            cell['p'] = 0.0 if depth <= 0.0 else min(1.0, 1.0 - math.exp(-4.0 * depth))
            cell['k'] = kind
            cell['h'] = 0.0
            if KIND_WALL in depths:
                touched = True
            if kind == KIND_PREY:
                eating = True
        body.eating = eating
        body.meal = best_meal if eating else None
        return touched

    def chew(self, body):
        """Bites land on a clock, as in the game: half a second, then one a second."""
        body.bite = None
        if not body.eating or body.meal is None:
            body.bite_left = FIRST_BITE
            return
        body.bite_left -= self.dt
        if body.bite_left > 0.0:
            return
        body.bite_left = BITE_INTERVAL
        kind, target = body.meal
        body.bite = kind
        self.bites[kind] = self.bites.get(kind, 0) + 1
        if target is not None and target.alive:
            target.hp -= math.ceil(JELLY_MAX_HP / JELLY_BITES)
            if target.hp <= 0:
                target.alive = False
                target.reason = 'eaten'
                self.deaths['jelly']['eaten'] += 1

    # -- stepping -------------------------------------------------------

    def observation(self):
        creatures = []
        for body in self.bodies:
            if not body.alive and body.reason is None:
                continue
            entry = dict(id=body.id, species=body.species,
                         state='dead' if not body.alive else 'spawned' if body.born else 'alive',
                         pos=[body.x, 0.0, body.z], heading=body.heading, speed=body.speed,
                         hp=body.hp, eating=body.eating,
                         ears=[0.0, 0.0, 0.0, 0.0],
                         cells=[dict(c) for c in body.cells])
            if not body.alive:
                entry['reason'] = body.reason
            if body.born and body.parent >= 0:
                entry['parent'] = body.parent
            if body.bite:
                entry['bite'] = body.bite
            creatures.append(entry)
        out = dict(v=1, type='observe', session='ecosystem', tick=self.tick,
                   t=self.time, dt=self.dt, creatures=creatures,
                   sounds=list(self.sounds), prey=list(self.prey))
        if self.pilot:
            out['player'] = dict(pos=[self.player[0], 0.0, self.player[1]], heading=0.0, hp=250, guard=False)
        return out

    def advance(self, body):
        if body.species == 'jelly':
            # A jelly runs at its own top speed; the rest is the same body physics.
            saved = self.run_speed
            self.run_speed = self.jelly_speed
            try:
                super().advance(body)
            finally:
                self.run_speed = saved
        else:
            super().advance(body)

    def step(self):
        self.update_world(self.time)
        living = self.alive()
        for body in living:
            if self.sense(body, living):
                body.contact_seconds += self.dt
            self.chew(body)
            if body.eating:
                body.eating_seconds += self.dt

        observation = self.observation()
        ecoli_only = dict(observation)
        ecoli_only['creatures'] = [c for c in observation['creatures'] if c['species'] == 'ecoli']
        command = self.colony.step(ecoli_only)
        orders = {c['id']: c for c in command['creatures']}
        for c in self.jellies.step(observation):
            orders[c['id']] = c
        self.rows.append(dict(tick=self.tick, t=round(self.time, 4), observe=observation,
                              command=protocol.make_command('ecosystem', self.tick, list(orders.values()))))

        for body in self.bodies:
            body.born = False
            if not body.alive:
                body.reason = None          # reported once
        wants_child = []
        for body in living:
            if not body.alive:
                continue
            order = orders.get(body.id)
            if order is None:
                continue
            body.mode = order['mode']
            body.command_speed = float(order.get('speed', 0.0))
            body.turn = float(order.get('turn', 0.0))
            if order.get('starved'):
                body.alive = False
                body.reason = 'starved'
                self.deaths[body.species]['starved'] += 1
            elif order.get('divide'):
                wants_child.append(body)
        for body in living:
            if body.alive:
                self.advance(body)
        for parent in wants_child:
            if parent.alive:
                self.place(parent.species, near=parent, parent=parent.id)

        self.tick += 1
        self.time += self.dt

    def run(self, seconds):
        steps = max(0, int(round(seconds * self.hz)))
        for _ in range(steps):
            self.step()
        return self.result()

    def result(self):
        return dict(seed=self.seed, ticks=self.tick, seconds=round(self.time, 3),
                    alive=dict(ecoli=len(self.alive('ecoli')), jelly=len(self.alive('jelly'))),
                    births=dict(self.births), deaths=self.deaths, bites=dict(self.bites),
                    plankton=round(self.field.total(PLANKTON), 3),
                    fingerprint=self.brain.fingerprint)

    # -- output ---------------------------------------------------------

    def write(self, out_dir, lineage_path=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        hello = dict(self.session.hello)
        hello['capacity'] = self.capacity['ecoli'] + self.capacity['jelly']
        hello['jelly'] = dict(hello.get('jelly') or {}, runSpeed=self.jelly_speed, capacity=self.capacity['jelly'])
        hello['pilot'] = dict(kind='ecosystem' if self.pilot else 'none', seed=self.seed, sortie=0)
        welcome = protocol.make_welcome('ecosystem', self.brain, channels=self.field.channels,
                                        max_creatures=hello['capacity'])
        (out_dir / 'meta.json').write_text(json.dumps(dict(
            schema=1, hello=hello, welcome=welcome, source=str(self.session.directory),
            started=datetime.datetime.now().isoformat(timespec='seconds')), indent=1) + '\n', encoding='utf-8')
        with (out_dir / 'link.jsonl').open('w', encoding='utf-8') as f:
            for row in self.rows:
                f.write(json.dumps(row, separators=(',', ':')))
                f.write('\n')
        summary = dict(reason='ecosystem_end', ticks=self.tick, result=self.result(),
                       colony=self.colony.summary(), jellies=self.jellies.summary())
        if lineage_path:
            kept = lineage.save(lineage_path, self.colony, self.jellies, session=out_dir)
            summary['lineage'] = dict(path=str(lineage_path), ecoli=len(kept['ecoli']), jelly=len(kept['jelly']))
        (out_dir / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n', encoding='utf-8')
        return out_dir


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.ecosystem',
                                description='Selection without learning, in a recorded arena')
    p.add_argument('--session', required=True, help='a recorded session directory (arena, pilot, wrecks)')
    p.add_argument('--brain', help='the fixed E. coli brain (default: seed 0)')
    p.add_argument('--seconds', type=float, default=300.0)
    p.add_argument('--runs', type=int, default=1, help='runs in a row, each from the last one\'s survivors')
    p.add_argument('--ecoli', type=int, default=6)
    p.add_argument('--jelly', type=int, default=6)
    p.add_argument('--capacity', type=int, default=16, help='pool per species')
    p.add_argument('--no-pilot', action='store_true', help='no pilot in the arena at all')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--lineage', default=str(ROOT / 'brains' / 'lineage.json'))
    p.add_argument('--no-lineage', action='store_true')
    p.add_argument('--out', default=str(ROOT / 'sessions' / 'ecosystem'))
    a = p.parse_args(argv)

    brain = EcoliBrain.from_json(a.brain) if a.brain else EcoliBrain(seed=a.seed)
    lineage_path = None if a.no_lineage else a.lineage
    for run in range(a.runs):
        world = Ecosystem(a.session, brain=brain, seed=a.seed + run, ecoli=a.ecoli, jelly=a.jelly,
                          ecoli_capacity=a.capacity, jelly_capacity=a.capacity,
                          pilot=not a.no_pilot, lineage_path=lineage_path)
        result = world.run(a.seconds)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        out = world.write(Path(a.out) / stamp, lineage_path)
        print(f"run {run + 1}/{a.runs}: {result['seconds']:.0f} s  alive ecoli {result['alive']['ecoli']} "
              f"jelly {result['alive']['jelly']}  births {result['births']}  deaths {result['deaths']}  "
              f"bites {result['bites']}  -> {out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
