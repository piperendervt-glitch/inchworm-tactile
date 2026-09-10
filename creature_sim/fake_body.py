"""A stand-in for Unity, so the Brain can be exercised without a headset.

It plays the Body role of the Creature Link Protocol: a flat 2D arena, six
creatures with the same sixteen tactile cells the real body has, and a player
parked at a fixed spot acting as bait.

    python -m creature_sim.fake_body --seconds 30

The arena defaults to the one in docs/creature-protocol-samples/hello.json, so
what runs here is the same geometry the conformance fixtures describe.
"""

import argparse
import json
import math
import random
import socket
import sys
import time
import uuid
from pathlib import Path

from . import protocol

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'docs' / 'creature-protocol-samples'

KIND_NONE, KIND_WALL, KIND_PREY, KIND_OTHER = 0, 1, 2, 3
CELLS = 16


def load_hello_arena(path=None):
    data = json.loads((Path(path) if path else SAMPLES / 'hello.json').read_text(encoding='utf-8'))
    return data['body'], data['arena'], data.get('player', {})


class Creature:
    def __init__(self, creature_id, x, z, heading, max_health):
        self.id = creature_id
        self.x = x
        self.z = z
        self.heading = heading
        self.speed = 0.0
        self.health = max_health
        self.state = 'spawned'
        self.reason = None
        self.eating = False
        self.mode = 'idle'
        self.command_speed = 0.0
        self.turn = 0.0
        self.path = 0.0
        self.contact_seconds = 0.0
        self.eating_seconds = 0.0
        self.cells = [dict(p=0.0, k=KIND_NONE, h=0.0) for _ in range(CELLS)]


class FakeBody:
    def __init__(self, host='127.0.0.1', port=47123,
                 count=6, seed=0, hz=30, hello_path=None, quiet=False):
        self.address = (host, port)
        self.count = count
        self.hz = hz
        self.dt = 1.0 / hz
        self.quiet = quiet
        self.rng = random.Random(f'fakebody:{seed}')
        self.session = str(uuid.uuid4())
        self.body, self.arena, self.player_spec = load_hello_arena(hello_path)
        self.bounds = [float(v) for v in self.arena['bounds']]
        self.obstacles = self.arena.get('obstacles') or []
        self.length = float(self.body.get('length', 3.0))
        self.radius = float(self.body.get('radius', 0.8))
        self.cell_radius = float(self.body.get('cellRadius', 0.4))
        self.run_speed = float(self.body.get('runSpeed', 6.0))
        self.max_health = int(self.body.get('maxHealth', 120))
        self.player = (0.0, 0.0)
        self.player_hp = int(self.player_spec.get('maxHealth', 250))
        self.tick = 0
        self.time = 0.0
        self.welcome = None
        self.fields = 0
        self.commands = 0
        self.creatures = [self._spawn(i) for i in range(count)]
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(0.5)

    def say(self, *parts):
        if not self.quiet:
            print(*parts, flush=True)

    def _spawn(self, creature_id):
        min_x, min_z, max_x, max_z = self.bounds
        for _ in range(500):
            x = self.rng.uniform(min_x + self.radius * 2, max_x - self.radius * 2)
            z = self.rng.uniform(min_z + self.radius * 2, max_z - self.radius * 2)
            if math.hypot(x - self.player[0], z - self.player[1]) < 8.0:
                continue
            if self._blocked(x, z, self.radius):
                continue
            return Creature(creature_id, x, z, self.rng.uniform(-math.pi, math.pi), self.max_health)
        raise RuntimeError('cannot place a creature; arena is too crowded')

    # -- geometry -------------------------------------------------------

    def _blocked(self, x, z, radius):
        min_x, min_z, max_x, max_z = self.bounds
        if not (min_x + radius <= x <= max_x - radius and min_z + radius <= z <= max_z - radius):
            return True
        return self._obstacle_depth(x, z, radius) > 0.0

    def _obstacle_depth(self, x, z, radius):
        """How far a disc at (x, z) is inside the nearest obstacle."""
        deepest = 0.0
        for obstacle in self.obstacles:
            cx, cz = obstacle['center']
            if obstacle['shape'] == 'circle':
                gap = math.hypot(x - cx, z - cz) - obstacle['radius']
            else:
                yaw = float(obstacle.get('yaw', 0.0))
                dx, dz = x - cx, z - cz
                lx = dx * math.cos(yaw) + dz * math.sin(yaw)
                lz = -dx * math.sin(yaw) + dz * math.cos(yaw)
                hx, hz = obstacle['size'][0] / 2.0, obstacle['size'][1] / 2.0
                ox, oz = abs(lx) - hx, abs(lz) - hz
                if ox > 0 or oz > 0:
                    gap = math.hypot(max(ox, 0.0), max(oz, 0.0))
                else:
                    gap = max(ox, oz)
            deepest = max(deepest, radius - gap)
        return deepest

    def _wall_depth(self, x, z, radius):
        min_x, min_z, max_x, max_z = self.bounds
        return max(0.0,
                   radius - (x - min_x), radius - (z - min_z),
                   radius - (max_x - x), radius - (max_z - z))

    def cell_world_xz(self, creature, index):
        """Same numbering as docs/creature-protocol.md section 5."""
        segment, ring = divmod(index, 8)
        phi = ring * math.pi / 4.0
        axial = (self.length / 4.0) * (1.0 if segment == 0 else -1.0)
        lateral = self.radius * math.sin(phi)
        h = creature.heading
        fx, fz = math.sin(h), math.cos(h)
        rx, rz = math.sin(h + math.pi / 2.0), math.cos(h + math.pi / 2.0)
        return creature.x + fx * axial + rx * lateral, creature.z + fz * axial + rz * lateral

    def sense(self, creature, others):
        """Fill the sixteen cells.

        Pressure grows with how deep the overlap is. Where several things touch
        one cell at once the reported kind follows the protocol's priority,
        prey over another creature over a wall, while the pressure is the
        deepest of them.
        """
        touched = False
        eating = False
        r = self.cell_radius
        for i, cell in enumerate(creature.cells):
            cx, cz = self.cell_world_xz(creature, i)
            depths = {}

            wall = max(self._wall_depth(cx, cz, r), self._obstacle_depth(cx, cz, r))
            if wall > 0.0:
                depths[KIND_WALL] = wall

            prey = r + 0.6 - math.hypot(cx - self.player[0], cz - self.player[1])
            if prey > 0.0:
                depths[KIND_PREY] = prey

            for other in others:
                if other is creature:
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
        creature.eating = eating
        return touched

    # -- stepping -------------------------------------------------------

    def advance(self, creature):
        dt = self.dt
        if creature.mode == 'tumble':
            creature.heading += creature.turn * dt
            creature.heading = (creature.heading + math.pi) % (2 * math.pi) - math.pi
            creature.speed = 0.0
            return
        target = self.run_speed * creature.command_speed if creature.mode == 'run' else 0.0
        # Same first-order lag as core.World, drag 5 per second.
        creature.speed += (target - creature.speed) * (1.0 - math.exp(-5.0 * dt))
        dx = creature.speed * dt * math.sin(creature.heading)
        dz = creature.speed * dt * math.cos(creature.heading)
        steps = max(1, math.ceil(math.hypot(dx, dz) / (self.radius * 0.25)))
        for _ in range(steps):
            nx, nz = creature.x + dx / steps, creature.z + dz / steps
            if self._blocked(nx, nz, self.radius):
                creature.speed = 0.0
                break
            creature.path += math.hypot(nx - creature.x, nz - creature.z)
            creature.x, creature.z = nx, nz

    def observation(self):
        alive = [c for c in self.creatures if c.state != 'dead']
        entries = []
        for creature in self.creatures:
            if creature.state == 'dead' and creature.reason is None:
                continue
            if creature.state != 'dead':
                if self.sense(creature, alive):
                    creature.contact_seconds += self.dt
                if creature.eating:
                    creature.eating_seconds += self.dt
            entry = dict(id=creature.id, state=creature.state,
                         pos=[round(creature.x, 4), 0.0, round(creature.z, 4)],
                         heading=round(creature.heading, 5),
                         speed=round(creature.speed, 4), hp=creature.health,
                         eating=creature.eating,
                         cells=[dict(p=round(c['p'], 4), k=c['k'], h=round(c['h'], 4))
                                for c in creature.cells])
            if creature.state == 'dead' and creature.reason:
                entry['reason'] = creature.reason
            entries.append(entry)
        return dict(v=1, type='observe', session=self.session, tick=self.tick,
                    t=round(self.time, 4), dt=round(self.dt, 6),
                    player=dict(pos=[self.player[0], 0.0, self.player[1]], heading=0.0,
                                hp=self.player_hp, guard=False),
                    creatures=entries)

    def apply(self, command):
        self.commands += 1
        by_id = {c.id: c for c in self.creatures}
        for entry in command.get('creatures', []):
            creature = by_id.get(entry['id'])
            if creature is None or creature.state == 'dead':
                continue
            creature.mode = entry['mode']
            creature.command_speed = float(entry.get('speed', 0.0))
            creature.turn = float(entry.get('turn', 0.0))
            if entry.get('starved'):
                creature.state = 'dead'
                creature.reason = 'starved'

    # -- run ------------------------------------------------------------

    def hello(self):
        return dict(v=1, type='hello', session=self.session, tick=0,
                    build='fake_body', hz=self.hz, body=self.body,
                    player=self.player_spec, arena=self.arena)

    def send(self, message):
        self.socket.sendto(protocol.encode(message), self.address)

    def receive(self, until):
        """Drain whatever has arrived, returning the newest command."""
        newest = None
        while True:
            timeout = until - time.monotonic()
            if timeout <= 0:
                return newest
            self.socket.settimeout(timeout)
            try:
                payload, _ = self.socket.recvfrom(65535)
            except socket.timeout:
                return newest
            message = protocol.parse(payload)
            kind = message.get('type')
            if kind == 'command':
                if newest is None or message['tick'] >= newest['tick']:
                    newest = message
            elif kind == 'welcome':
                self.welcome = message
                self.say(f"[welcome] species={message['brain']['species']} "
                         f"generation={message['brain']['generation']} "
                         f"field={message['field']['cols']}x{message['field']['rows']}")
            elif kind == 'field':
                self.fields += 1
            elif kind == 'error':
                self.say(f"[error] {message.get('code')}: {message.get('message')}")

    def run(self, seconds=30.0):
        self.send(self.hello())
        self.receive(time.monotonic() + 1.0)
        if self.welcome is None:
            self.say('[warn] no welcome; is the server running?')

        # Start the frame clock after the handshake. Starting it before it
        # would leave the first second of frames already past their deadline,
        # so they would all fire at once and none would wait for a command.
        start = time.monotonic()
        ticks = int(seconds * self.hz)
        for _ in range(ticks):
            frame_end = start + (self.tick + 1) * self.dt
            self.send(self.observation())
            for creature in self.creatures:
                if creature.state == 'spawned':
                    creature.state = 'alive'
                elif creature.state == 'dead':
                    creature.reason = None
            command = self.receive(frame_end)
            if command is not None:
                self.apply(command)
            for creature in self.creatures:
                if creature.state == 'alive':
                    self.advance(creature)
            self.tick += 1
            self.time += self.dt
            rest = frame_end - time.monotonic()
            if rest > 0:
                time.sleep(rest)

        self.send(protocol.make_bye(self.session, self.tick, 'mission_end'))
        self.report()
        self.socket.close()

    def report(self):
        print()
        print(f'ticks {self.tick}  commands {self.commands}  field messages {self.fields}')
        print(f'{"id":>3} {"path m":>9} {"contact s":>10} {"eating s":>9} {"state":>8}')
        for creature in self.creatures:
            print(f'{creature.id:>3} {creature.path:>9.2f} {creature.contact_seconds:>10.2f} '
                  f'{creature.eating_seconds:>9.2f} {creature.state:>8}')
        moved = sum(c.path for c in self.creatures)
        print(f'total path {moved:.2f} m over {self.time:.1f} s')


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.fake_body',
                                description='Body-side stand-in for Unity')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=47123)
    p.add_argument('--seconds', type=float, default=30.0)
    p.add_argument('--count', type=int, default=6)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--hz', type=int, default=30)
    p.add_argument('--hello', help='hello.json to take the arena from')
    p.add_argument('--quiet', action='store_true')
    a = p.parse_args(argv)
    body = FakeBody(host=a.host, port=a.port, count=a.count, seed=a.seed, hz=a.hz,
                    hello_path=a.hello, quiet=a.quiet)
    body.run(seconds=a.seconds)
    return 0


if __name__ == '__main__':
    sys.exit(main())
