"""Jellies: swimmers with no brain at all, only a nerve ring.

The locomotion is the jellyfish prototype's excitable ring (Jelly_test,
excitable_ring.py and j2_wave_amplitude.py). Eight cells round the body in the
swimming plane; a firing cell excites its neighbours, then rests. A poke on
the skin fires the cell facing it, waves run both ways round and meet on the
far side, and every firing cell pushes: the sum is a thrust away from the
poke. Pacemakers set by the genome fire on their own, so a jelly swims
steadily away from wherever its pacemaker sits, and a poke bends its course
for a moment before the pacemaker takes over again.

The ring is integrated in body frame and turned into what the protocol can
carry: the Body is asked to face the way the accumulated thrust points and to
run at its strength. A pacemaker on the flank therefore makes a jelly that
circles; two opposite ones, one that hovers.

A jelly eats nothing it can touch. It grazes the plankton channel of the
field, which wrecks shed, and it is itself food for an E. coli.
"""

import math
import random

from .. import genome as genes
from ..field import PLANKTON, StigmergyField
from ..physiology import DEFAULTS as PHYSIOLOGY, Physiology

RING = 8
KIND_NONE, KIND_WALL, KIND_PREY, KIND_OTHER = 0, 1, 2, 3

DEFAULTS = dict(
    initial_energy=70.0,
    max_energy=PHYSIOLOGY['max_energy'],
    basal_cost=2.0,          # jellies burn faster than E. coli: the prototype's quick turnover
    motion_cost=1.0,
    fed_seconds=PHYSIOLOGY['fed_seconds'],
    divide_cooldown=PHYSIOLOGY['divide_cooldown'],
    child_share=PHYSIOLOGY['child_share'],
    # Grazing: plankton units a second at suck 1.0, and energy per unit.
    graze_rate=0.6,
    plankton_energy=40.0,
    # What a wreck sheds per second, and the most one cell holds.
    plankton_rate=0.8,
    plankton_cap=6.0,
    # The nerve ring, from excitable_ring.py: threshold, decay, coupling,
    # refractory ticks, amplitude loss per hop, and its tick length.
    # g is lower than the prototype's 0.85 because this ring has eight cells,
    # not sixteen: the far side must be clearly weaker than the near side or
    # the push comes out backwards. 0.6^4 leaves the far side a seventh.
    theta=0.5, delta=0.5, k=0.6, r0=6, g=0.6,
    ring_dt=0.1,
    # Thrust and water: an impulse per firing, drag per ring tick, top speed.
    impulse=0.9, drag=0.12,
    turn_gain=4.0, max_turn=3.0,
    run_speed=3.0,
    field_scale=4.0,
)

# Skin cell -> ring cell. The skin sits round the long axis (ring 0 up, 2
# right, 4 down, 6 left) in two segments (0 front, 1 rear). What matters for
# swimming is the horizontal direction of a poke: lateral from the ring's sine,
# axial from the segment. Ring cell 0 is straight ahead, going clockwise.
def _ring_cell(skin_index):
    segment, ring = divmod(skin_index, RING)
    lateral = math.sin(ring * math.pi / 4.0)
    axial = 1.0 if segment == 0 else -1.0
    angle = math.atan2(lateral, axial)            # clockwise from forward
    return int(round(angle / (2 * math.pi / RING))) % RING


SKIN_TO_RING = tuple(_ring_cell(i) for i in range(16))


class Jelly:
    __slots__ = ('id', 'rng', 'genome', 'body', 'E', 'R', 'A', 'timers', 'vx', 'vz',
                 'tx', 'tz', 'ring_left', 'alive_seconds', 'grazed', 'fired', 'pokes')

    def __init__(self, creature_id, settings, rng, genome=None, energy=None):
        self.id = creature_id
        self.rng = rng
        self.genome = genome if genome is not None else genes.founder('jelly', rng)
        self.body = Physiology(settings['initial_energy'] if energy is None else energy)
        self.E = [0.0] * RING
        self.R = [0] * RING
        self.A = [0.0] * RING
        # Pacemaker cells: founders get one or two at random spots; the
        # period comes from the genome and jitters a little per cell.
        count = 1 + (1 if rng.random() < 0.4 else 0)
        cells = rng.sample(range(RING), count)
        self.timers = [[c, 1 + rng.randrange(int(self.genome['pace']))] for c in cells]
        self.vx = self.vz = 0.0
        self.tx = self.tz = 0.0            # thrust gathered this tick, body frame
        self.ring_left = 0.0
        self.alive_seconds = 0.0
        self.grazed = 0.0
        self.fired = 0
        self.pokes = 0

    @property
    def energy(self):
        return self.body.energy

    @property
    def starved(self):
        return self.body.starved


class JellyColony:
    def __init__(self, field=None, bounds=None, seed=0, settings=None):
        self.settings = dict(DEFAULTS)
        self.settings.update(settings or {})
        if field is None:
            if bounds is None:
                raise ValueError('JellyColony needs a field or bounds')
            field = StigmergyField(bounds)
        if field.channels <= PLANKTON:
            raise ValueError('the field has no plankton channel')
        self.field = field
        self.seed = seed
        self.jellies = {}
        self.tick = 0
        self.time = 0.0
        self.deaths = 0
        self.births = 0

    # -- the nerve ring ---------------------------------------------------

    def ring_step(self, j):
        """One synchronous update of the ring. Returns (cell, amplitude) that fired."""
        s = self.settings
        E, R, A = j.E, j.R, j.A
        nE = [0.0] * RING
        nR = [0] * RING
        nA = [0.0] * RING
        fired = []
        for i in range(RING):
            if R[i] > 0:
                nR[i] = R[i] - 1
                continue
            inp = 0.0
            src = 0.0
            for n in (i - 1, (i + 1) % RING):
                if E[n] >= 1.0:
                    inp += s['k']
                    src = max(src, A[n])
            e = E[i] * s['delta'] + inp
            if e >= s['theta']:
                nE[i] = 1.0
                nR[i] = s['r0']
                nA[i] = src * s['g']
                fired.append((i, nA[i]))
            else:
                nE[i] = e
        j.E, j.R, j.A = nE, nR, nA
        return fired

    def push(self, j, cell, amp):
        """A firing cell pushes the body away from itself. Body frame: (right, forward)."""
        a = cell * 2 * math.pi / RING              # clockwise from forward
        j.tx -= amp * self.settings['impulse'] * math.sin(a)
        j.tz -= amp * self.settings['impulse'] * math.cos(a)
        j.fired += 1

    def stimulate(self, j, cell):
        """Fire a cell from outside (a poke or a pacemaker). It pushes at once."""
        if j.R[cell] == 0:
            j.E[cell] = 1.0
            j.A[cell] = 1.0
            j.R[cell] = self.settings['r0']
            self.push(j, cell, 1.0)
            return True
        return False

    # -- stepping ---------------------------------------------------------

    def step(self, observation):
        dt = float(observation.get('dt', 1.0 / 30.0))
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be nonnegative and finite')
        dt = min(dt, 0.1)
        self.tick = int(observation.get('tick', self.tick + 1))
        self.time = float(observation.get('t', self.time + dt))

        # Wrecks shed plankton. E. coli do not eat it; jellies do.
        s = self.settings
        for item in observation.get('prey') or ():
            if item.get('kind') != 'wreck':
                continue
            pos = item.get('pos') or (0.0, 0.0, 0.0)
            here = self.field.sample(float(pos[0]), float(pos[2]), PLANKTON)
            if here < s['plankton_cap']:
                self.field.deposit(float(pos[0]), float(pos[2]), PLANKTON, s['plankton_rate'] * dt)

        seen = set()
        out = []
        for observed in observation.get('creatures', []):
            if observed.get('species', 'ecoli') != 'jelly':
                continue
            creature_id = observed['id']
            if observed.get('state', 'alive') == 'dead':
                self.jellies.pop(creature_id, None)
                self.deaths += 1
                continue
            if observed.get('state') == 'spawned' or creature_id not in self.jellies:
                rng = random.Random(f'jelly:{self.seed}:{creature_id}:{self.tick}')
                parent = self.jellies.get(observed.get('parent', -1))
                if parent is not None:
                    share = parent.body.split(s)
                    child = Jelly(creature_id, s, rng, genome=genes.mutate(parent.genome, rng), energy=share)
                    # Pacemakers are inherited too, and may migrate a cell.
                    child.timers = [[(c + (rng.choice((-1, 1)) if rng.random() < 0.15 else 0)) % RING,
                                     1 + rng.randrange(int(child.genome['pace']))]
                                    for c, _ in parent.timers]
                    self.jellies[creature_id] = child
                    self.births += 1
                else:
                    self.jellies[creature_id] = Jelly(creature_id, s, rng)
            seen.add(creature_id)
            out.append(self._step_one(self.jellies[creature_id], observed, dt))
        for creature_id in list(self.jellies):
            if creature_id not in seen:
                del self.jellies[creature_id]
        return out

    def _step_one(self, j, observed, dt):
        s = self.settings
        x, _, z = observed['pos']
        heading = float(observed['heading'])
        speed = float(observed.get('speed', 0.0))
        j.alive_seconds += dt

        # Pokes: skin pressed harder than this jelly's nociceptor threshold.
        j.tx = j.tz = 0.0
        for i, cell in enumerate(observed.get('cells') or ()):
            p = float(cell.get('p', 0.0))
            if p >= j.genome['noci'] and self.stimulate(j, SKIN_TO_RING[i]):
                j.pokes += 1

        # The ring runs on its own clock, several ticks a game tick at most.
        j.ring_left += dt
        while j.ring_left >= s['ring_dt']:
            j.ring_left -= s['ring_dt']
            for timer in j.timers:
                timer[1] -= 1
                if timer[1] <= 0:
                    timer[1] = max(2, int(j.genome['pace']))
                    self.stimulate(j, timer[0])
            for cell, amp in self.ring_step(j):
                self.push(j, cell, amp)
            j.vx *= 1.0 - s['drag']
            j.vz *= 1.0 - s['drag']
        # Body frame to world: forward is (sin h, cos h), right is (cos h, -sin h).
        fx, fz = math.sin(heading), math.cos(heading)
        rx, rz = math.cos(heading), -math.sin(heading)
        j.vx += j.tx * rx + j.tz * fx
        j.vz += j.tx * rz + j.tz * fz

        # Grazing.
        grazed = self.field.take(x, z, PLANKTON, s['graze_rate'] * j.genome['suck'] * dt)
        j.grazed += grazed
        j.body.feed(grazed * s['plankton_energy'], s['max_energy'])

        # What the Body can do with it: face the drift and run at its strength.
        want = math.hypot(j.vx, j.vz)
        run = min(1.0, want) * j.genome['speed']
        if want > 1e-3:
            bearing = math.atan2(j.vx, j.vz)
            error = (bearing - heading + math.pi) % (2 * math.pi) - math.pi
            turn = max(-s['max_turn'], min(s['max_turn'], error * s['turn_gain']))
        else:
            turn = 0.0
        mode = 'run' if run > 0.02 else 'idle'

        motion = min(1.0, abs(speed) / s['run_speed']) if s['run_speed'] > 0 else run
        divide = j.body.step(dt, s, j.genome['metab'], motion, j.genome['satiety'])
        if j.starved:
            mode, run, turn = 'idle', 0.0, 0.0
        return dict(id=j.id, mode=mode, speed=run, turn=turn, deposit=0.0,
                    energy=j.energy / s['max_energy'], starved=j.starved, divide=divide)

    def summary(self):
        return dict(tick=self.tick, alive=len(self.jellies), deaths_recorded=self.deaths,
                    births=self.births,
                    creatures=[dict(id=j.id, energy=j.energy, alive_s=j.alive_seconds,
                                    grazed=j.grazed, fired=j.fired, pokes=j.pokes,
                                    divisions=j.body.divisions, pacemakers=[c for c, _ in j.timers],
                                    genome=genes.rounded(j.genome))
                               for j in self.jellies.values()])
