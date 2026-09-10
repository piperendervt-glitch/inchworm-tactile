"""Replay a recorded session so candidate brains can be scored against it.

Learning happens after the game, never during it, so the creatures in a log
were driven by whatever brain played that day. Their behaviour cannot be used
as an example to copy. What the log holds that is worth keeping is the world:
the arena, and the path the pilot took through it.

So a replay puts a fresh colony into that arena and moves the pilot along the
recorded path as a piece of walking bait. The pilot does not react, which is
the honest limit of this approach: nothing that depends on the player
responding can be learned here.
"""

import math
import random

from .ecoli.brain import EcoliBrain
from .ecoli.colony import Colony
from .field import StigmergyField
from .logs import Session

CELLS = 16
KIND_NONE, KIND_WALL, KIND_PREY, KIND_OTHER = 0, 1, 2, 3

# What a run is worth. Eating is the point; reaching the prey quickly and
# staying alive are worth something; grinding along a wall is not.
WEIGHT_EATING = 10.0
WEIGHT_FIRST_CONTACT = 20.0
WEIGHT_ALIVE = 0.5
WEIGHT_CONTACT = 1.0


class Body:
    """A flat stand-in for the Unity body, with the same sixteen cells."""

    __slots__ = ('id', 'x', 'z', 'heading', 'speed', 'alive', 'eating',
                 'mode', 'command_speed', 'turn', 'path', 'contact_seconds',
                 'eating_seconds', 'first_contact', 'cells')

    def __init__(self, creature_id, x, z, heading):
        self.id = creature_id
        self.x = x
        self.z = z
        self.heading = heading
        self.speed = 0.0
        self.alive = True
        self.eating = False
        self.mode = 'idle'
        self.command_speed = 0.0
        self.turn = 0.0
        self.path = 0.0
        self.contact_seconds = 0.0
        self.eating_seconds = 0.0
        self.first_contact = None
        self.cells = [dict(p=0.0, k=KIND_NONE, h=0.0) for _ in range(CELLS)]


class ReplayWorld:
    """A colony let loose in a recorded arena, chasing a recorded pilot."""

    def __init__(self, session, brain=None, count=6, seed=0, hz=30,
                 settings=None, cols=64, rows=64):
        self.session = session if isinstance(session, Session) else Session(session)
        self.track = self.session.player_track()
        if not self.track:
            raise ValueError('the session has no player track to chase')
        self.bounds = self.session.bounds
        self.obstacles = self.session.obstacles
        body = self.session.body
        self.length = float(body.get('length', 3.0))
        self.radius = float(body.get('radius', 0.8))
        self.cell_radius = float(body.get('cellRadius', 0.4))
        self.run_speed = float(body.get('runSpeed', 6.0))

        self.hz = hz
        self.dt = 1.0 / hz
        self.seed = seed
        self.rng = random.Random(f'replay:{seed}')
        self.brain = brain if brain is not None else EcoliBrain(seed=seed)
        self.field = StigmergyField(self.bounds, cols=cols, rows=rows)
        colony_settings = dict(length=self.length, radius=self.radius,
                               run_speed=self.run_speed)
        colony_settings.update(settings or {})
        self.colony = Colony(brain=self.brain, field=self.field, seed=seed,
                             settings=colony_settings)
        self.tick = 0
        self.time = 0.0
        self.player = (self.track[0][1], self.track[0][2])
        self.bodies = [self._spawn(i) for i in range(count)]
        self.deaths = 0

    # -- placing --------------------------------------------------------

    def _spawn(self, creature_id):
        min_x, min_z, max_x, max_z = self.bounds
        for _ in range(500):
            x = self.rng.uniform(min_x + self.radius * 2, max_x - self.radius * 2)
            z = self.rng.uniform(min_z + self.radius * 2, max_z - self.radius * 2)
            if math.hypot(x - self.player[0], z - self.player[1]) < 8.0:
                continue
            if self._blocked(x, z, self.radius):
                continue
            return Body(creature_id, x, z, self.rng.uniform(-math.pi, math.pi))
        raise RuntimeError('cannot place a creature; the arena is too crowded')

    def _blocked(self, x, z, radius):
        min_x, min_z, max_x, max_z = self.bounds
        if not (min_x + radius <= x <= max_x - radius and min_z + radius <= z <= max_z - radius):
            return True
        return self._obstacle_depth(x, z, radius) > 0.0

    def _obstacle_depth(self, x, z, radius):
        deepest = 0.0
        for obstacle in self.obstacles:
            cx, cz = obstacle.get('center', (0.0, 0.0))
            if obstacle.get('shape') == 'circle':
                gap = math.hypot(x - cx, z - cz) - float(obstacle.get('radius', 1.0))
            else:
                yaw = float(obstacle.get('yaw', 0.0))
                dx, dz = x - cx, z - cz
                lx = dx * math.cos(yaw) + dz * math.sin(yaw)
                lz = -dx * math.sin(yaw) + dz * math.cos(yaw)
                size = obstacle.get('size', (1.0, 1.0))
                hx, hz = float(size[0]) / 2.0, float(size[1]) / 2.0
                ox, oz = abs(lx) - hx, abs(lz) - hz
                gap = (math.hypot(max(ox, 0.0), max(oz, 0.0)) if ox > 0 or oz > 0
                       else max(ox, oz))
            deepest = max(deepest, radius - gap)
        return deepest

    def _wall_depth(self, x, z, radius):
        min_x, min_z, max_x, max_z = self.bounds
        return max(0.0, radius - (x - min_x), radius - (z - min_z),
                   radius - (max_x - x), radius - (max_z - z))

    def cell_world_xz(self, body, index):
        return self.colony.cell_world_xz(body.x, body.z, body.heading, index)

    # -- sensing --------------------------------------------------------

    def sense(self, body, others):
        touched = False
        eating = False
        r = self.cell_radius
        for i, cell in enumerate(body.cells):
            cx, cz = self.cell_world_xz(body, i)
            depths = {}
            wall = max(self._wall_depth(cx, cz, r), self._obstacle_depth(cx, cz, r))
            if wall > 0.0:
                depths[KIND_WALL] = wall
            prey = r + 0.6 - math.hypot(cx - self.player[0], cz - self.player[1])
            if prey > 0.0:
                depths[KIND_PREY] = prey
            for other in others:
                if other is body or not other.alive:
                    continue
                gap = r + self.radius - math.hypot(cx - other.x, cz - other.z)
                if gap > 0.0:
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
        return touched

    # -- stepping -------------------------------------------------------

    def observation(self):
        creatures = []
        for body in self.bodies:
            if not body.alive:
                continue
            creatures.append(dict(
                id=body.id, state='spawned' if self.tick == 0 else 'alive',
                pos=[body.x, 0.0, body.z], heading=body.heading, speed=body.speed,
                hp=120, eating=body.eating,
                cells=[dict(c) for c in body.cells]))
        return dict(v=1, type='observe', session='replay', tick=self.tick,
                    t=self.time, dt=self.dt,
                    player=dict(pos=[self.player[0], 0.0, self.player[1]],
                                heading=0.0, hp=250, guard=False),
                    creatures=creatures)

    def advance(self, body):
        dt = self.dt
        if body.mode == 'tumble':
            body.heading += body.turn * dt
            body.heading = (body.heading + math.pi) % (2 * math.pi) - math.pi
            body.speed = 0.0
            return
        target = self.run_speed * body.command_speed if body.mode == 'run' else 0.0
        body.speed += (target - body.speed) * (1.0 - math.exp(-5.0 * dt))
        dx = body.speed * dt * math.sin(body.heading)
        dz = body.speed * dt * math.cos(body.heading)
        steps = max(1, math.ceil(math.hypot(dx, dz) / (self.radius * 0.25)))
        for _ in range(steps):
            nx, nz = body.x + dx / steps, body.z + dz / steps
            if self._blocked(nx, nz, self.radius):
                body.speed = 0.0
                break
            body.path += math.hypot(nx - body.x, nz - body.z)
            body.x, body.z = nx, nz

    def player_at(self, seconds):
        """Where the pilot was, held at the ends rather than looping."""
        if seconds <= self.track[0][0]:
            return self.track[0][1], self.track[0][2]
        if seconds >= self.track[-1][0]:
            return self.track[-1][1], self.track[-1][2]
        # The track is dense and in order, so a walk from the last index is
        # cheaper than a search.
        index = getattr(self, '_cursor', 0)
        while index + 1 < len(self.track) and self.track[index + 1][0] < seconds:
            index += 1
        self._cursor = index
        a, b = self.track[index], self.track[min(index + 1, len(self.track) - 1)]
        span = b[0] - a[0]
        f = 0.0 if span <= 0 else (seconds - a[0]) / span
        return a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f

    def step(self):
        self.player = self.player_at(self.time)
        alive = [b for b in self.bodies if b.alive]
        for body in alive:
            if self.sense(body, alive):
                body.contact_seconds += self.dt
            if body.eating:
                body.eating_seconds += self.dt
                if body.first_contact is None:
                    body.first_contact = self.time

        command = self.colony.step(self.observation())
        orders = {c['id']: c for c in command['creatures']}
        for body in alive:
            order = orders.get(body.id)
            if order is None:
                continue
            body.mode = order['mode']
            body.command_speed = float(order.get('speed', 0.0))
            body.turn = float(order.get('turn', 0.0))
            if order.get('starved'):
                body.alive = False
                self.deaths += 1
        for body in alive:
            if body.alive:
                self.advance(body)

        self.tick += 1
        self.time += self.dt

    def run(self, seconds=None):
        """Play the whole recording, or the first ``seconds`` of it."""
        limit = self.track[-1][0] if seconds is None else min(seconds, self.track[-1][0])
        # The number of steps is settled up front. Accumulating the clock and
        # comparing it against the limit would run one step too many or too
        # few depending on which way the rounding fell.
        steps = max(0, int(round(limit * self.hz)))
        for _ in range(steps):
            self.step()
        return self.result()

    # -- scoring --------------------------------------------------------

    def result(self):
        eating = sum(b.eating_seconds for b in self.bodies)
        contact = sum(b.contact_seconds for b in self.bodies)
        path = sum(b.path for b in self.bodies)
        firsts = [b.first_contact for b in self.bodies if b.first_contact is not None]
        return dict(seed=self.seed, ticks=self.tick, seconds=round(self.time, 3),
                    creatures=len(self.bodies), alive=sum(1 for b in self.bodies if b.alive),
                    deaths=self.deaths, eating_s=round(eating, 3),
                    contact_s=round(contact, 3), path_m=round(path, 3),
                    reached=len(firsts),
                    first_contact_s=round(min(firsts), 3) if firsts else None,
                    fingerprint=self.brain.fingerprint)


def fitness(result):
    """One number for how well a brain did on a replay.

    Eating dominates. Reaching the prey early counts, and so does staying
    alive. Time spent pressed against a wall counts against.
    """
    seconds = max(1e-6, result['seconds'])
    score = WEIGHT_EATING * result['eating_s']
    first = result.get('first_contact_s')
    if first is not None:
        # Full marks for touching at once, none for touching only at the end.
        score += WEIGHT_FIRST_CONTACT * max(0.0, 1.0 - first / seconds)
    score += WEIGHT_ALIVE * result['alive'] * seconds / max(1, result['creatures'])
    score -= WEIGHT_CONTACT * result['contact_s'] / max(1, result['creatures'])
    return score


def evaluate(session, brain, count=6, seed=0, seconds=None, settings=None):
    """Score one brain on one recording."""
    world = ReplayWorld(session, brain=brain, count=count, seed=seed, settings=settings)
    return fitness(world.run(seconds)), world.result()
