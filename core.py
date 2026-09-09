"""30 Hz tactile-only distributed controller and explicitly approximate mechanics."""
import copy
import csv
import json
import math
import random
from pathlib import Path
from locomotion import PadMechanics, shape, reference_angles, reference_grips
from layout import resolve_layout,finite

DT = 1 / 30
N = 5
LENGTH = 0.045


def read_config(path=None):
    config = json.loads(Path(path or Path(__file__).with_name('config.json')).read_text())
    sensor = config['sensor']
    if sensor['delay_frames'] not in (1, 2):
        raise ValueError('delay_frames must be 1 or 2')
    if sensor['pitch_m'] != 0.015:
        raise ValueError('This 3x3 body model requires 15 mm pitch')
    if not 0 <= sensor['baseline'] < 1 or sensor['gain'] <= 0 or sensor['noise'] < 0:
        raise ValueError('Invalid sensor parameters')
    return config


class LocalNCA:
    """Shared local recurrent rule; four state/communication channels per segment.

    A seeded oscillator is an explicit locomotion prior, not learned intelligence.
    Only local brightness and adjacent four-channel states enter this rule.
    """
    DEFAULT = [0.14, 0.18, 0.55, 0.8, 0.7, 0.45, 0.35, 0.65]

    def __init__(self, weights=None):
        self.weights = list(weights or self.DEFAULT)
        self.state = [[1., 0., 0., 0.] for i in range(N)]
        self.grips = [False, False]
        self.features = [[0., 0., 0., 0.] for _ in range(N)]

    def step(self, belly, head):
        old = self.state
        new = []
        a, coupling, amplitude, touch_gain, rough_gain, memory, yaw_gain, front_gain = self.weights
        for i in range(N):
            cells = belly[i * 9:(i + 1) * 9]
            mean = sum(cells) / 9
            rough = math.sqrt(sum((v - mean) ** 2 for v in cells) / 9)
            front = max(0., sum(head) / 9 - .15) if i == N - 1 else 0.
            lateral = sum(head[r * 3] - head[r * 3 + 2] for r in range(3)) / 3 if i == N - 1 else 0.
            self.features[i] = [mean, rough, front, lateral]
            neighbors = [old[j] for j in (i - 1, i + 1) if 0 <= j < N]
            neighbor = [sum(s[k] for s in neighbors) / len(neighbors) for k in range(4)]
            phase = math.atan2(old[i][1], old[i][0]) + max(.015, abs(a)) * (.65 + .35 * min(1., mean / .4))
            # Synchronize the local arch oscillators through neighbor states.
            for j in (i - 1, i + 1):
                if 0 <= j < N:
                    phase += coupling * math.sin(math.atan2(old[j][1], old[j][0]) - math.atan2(old[i][1], old[i][0])) / len(neighbors)
            contact = math.tanh(touch_gain * (mean - .3) + rough_gain * rough + memory * old[i][2] + .1 * neighbor[2])
            turn = math.tanh(memory * old[i][3] + yaw_gain * lateral + front_gain * front * (rough + .08) + .1 * neighbor[3])
            new.append([math.cos(phase), math.sin(phase), contact, turn])
        self.state = new
        pitch = [-min(.75, abs(amplitude)) * (1 - s[0]) / 2 for s in new]
        # Optional gripper outputs, gated only by local tactile contact and phase.
        self.grips = [max(belly[:9]) > .16 and new[0][1] <= .12,
                      max(belly[-9:]) > .16 and new[-1][1] >= -.12]
        return pitch + [new[-1][3] * .8]


class World:
    def __init__(self, config=None, weights=None):
        self.config = copy.deepcopy(config or read_config())
        self.rng = random.Random(self.config['seed'])
        if 'layout' in self.config:
            spawn,self.objects=resolve_layout(self.config['layout'])
        else:
            spawn={k:finite(self.config.get('spawn',{}).get(k,0.),k) for k in ('x','y','heading_deg')}
            self.objects=copy.deepcopy(self.config['environment'])
        self.initial_spawn=copy.deepcopy(spawn)
        self.controller = LocalNCA(weights)
        self.mechanics = PadMechanics(self.config.get('physics'))
        self.control_mode = 'ai'
        self.distance = 0.
        self.blocked = False
        self.tick = 0
        self.hp = 100.
        self.x=spawn['x'];self.y=spawn['y'];self.heading=math.radians(spawn['heading_deg'])
        self.angles = [0.] * 6
        self.targets = [0.] * 6
        self.belly = [self.config['sensor']['baseline']] * 45
        self.head = [self.config['sensor']['baseline']] * 9
        self.queue = [(self.belly[:], self.head[:]) for _ in range(self.config['sensor']['delay_frames'])]
        self.food_hold = {}
        self.nodes = self.geometry()
        self.initial_center=tuple((self.nodes[0][i]+self.nodes[-1][i])/2 for i in range(3))

    def geometry(self):
        local = shape(self.angles[:5])
        height = self.mechanics.settings['pad_height_m']
        return [(self.x + u * math.cos(self.heading), self.y + u * math.sin(self.heading), z + height) for u,z in local]

    def material_at(self, x, y):
        for idx, obj in enumerate(self.objects):
            if not obj.get('eaten') and math.hypot(x - obj['x'], y - obj['y']) <= obj['radius']:
                return obj['kind'], idx, obj['height']
        return 'ground', -1, 0.

    def brightness(self, indentation, kind, x, y):
        s = self.config['sensor']
        mat = self.config['materials'][kind]
        texture = 1 + mat['roughness'] * math.sin(x * 1800 + .6) * math.cos(y * 1600 + .3)
        load = max(0., indentation) * mat['hardness'] * texture
        return max(0., min(1., s['baseline'] + (1 - s['baseline']) * (1 - math.exp(-s['gain'] * load)) + self.rng.gauss(0, s['noise'])))

    def sense(self):
        belly, head, harmful = [], [], False
        pitch = self.config['sensor']['pitch_m']
        for i in range(N):
            start, end = self.nodes[i + 1:i + 3]
            for row in range(3):
                t = (row + .5) / 3
                for col in range(3):
                    lateral = (col - 1) * pitch
                    x = start[0] * (1 - t) + end[0] * t - lateral * math.sin(self.heading)
                    y = start[1] * (1 - t) + end[1] * t + lateral * math.cos(self.heading)
                    z = start[2] * (1 - t) + end[2] * t
                    kind, _, height = self.material_at(x, y)
                    indentation = max(0., (.014 + height - z) / .018)
                    # End module cells are mechanically coupled to the compliant pads.
                    # Normal load is converted to indentation, never exposed directly to AI.
                    if i in (0, N-1):
                        pad = 0 if i == 0 else 1
                        px,py,_ = self.nodes[0 if pad == 0 else -1]
                        kind,_,height = self.material_at(px,py)
                        indentation = self.mechanics.loads[pad] / .9
                    harmful |= kind == 'harm' and indentation > 0
                    belly.append(self.brightness(indentation, kind, x, y))
        hx, hy, hz = self.nodes[-1]
        facing = self.heading + self.angles[5]
        food_contacts = set()
        for row in range(3):
            for col in range(3):
                lateral = (col - 1) * pitch
                x = hx + .014 * math.cos(facing) - lateral * math.sin(facing)
                y = hy + .014 * math.sin(facing) + lateral * math.cos(facing)
                kind, idx, height = self.material_at(x, y)
                z = hz + row * pitch
                indentation = max(0., (height + .01 - z) / .025) if idx >= 0 else 0.
                harmful |= kind == 'harm' and indentation > 0
                if kind == 'food' and indentation > 0:
                    food_contacts.add(idx)
                head.append(self.brightness(indentation, kind, x, y))
        for idx in range(len(self.objects)):
            self.food_hold[idx] = self.food_hold.get(idx, 0) + DT if idx in food_contacts else 0.
            if self.food_hold[idx] >= 1.:
                self.objects[idx]['eaten'] = True
                self.hp = min(100., self.hp + 25.)
        self.hp = max(0., self.hp - DT * (.6 + (12. if harmful else 0.)))
        self.queue.append((belly, head))
        self.belly, self.head = self.queue.pop(0)

    def step(self, manual=None, mode='ai', manual_grips=(False,False)):
        if self.hp <= 0:
            return None
        observation = self.belly[:] + self.head[:]
        self.control_mode = 'manual' if manual is not None else mode
        if manual is not None:
            self.targets = list(manual)
            grips = manual_grips
        elif mode == 'reference':
            self.targets = reference_angles((self.tick+1)*DT,self.mechanics.settings['strategy'])
            grips = reference_grips((self.tick+1)*DT)
        else:
            self.targets = self.controller.step(self.belly, self.head)
            grips = self.controller.grips
        if len(self.targets) != 6 or not all(math.isfinite(v) for v in self.targets):
            raise ValueError('Six finite target angles required')
        self.targets = [max(-.9,min(.9,v)) for v in self.targets]
        rate = self.mechanics.settings['joint_rate_rad_s']*DT
        candidate = [v+max(-rate,min(rate,t-v)) for v,t in zip(self.angles,self.targets)]
        old_angles = self.angles[:]
        old_x,old_y = self.x,self.y
        old_slip_total = self.mechanics.slip_total[:]
        self.angles,forward = self.mechanics.advance(old_angles,candidate,DT,grips)
        self.x += forward*math.cos(self.heading)
        self.y += forward*math.sin(self.heading)
        candidate_nodes = self.geometry()
        # Conservative stop for contact with any body rod; no terrain climbing.
        self.blocked = False
        for o in self.objects:
            if o['kind'] != 'obstacle':
                continue
            for a,b in zip(candidate_nodes,candidate_nodes[1:]):
                vx,vy=b[0]-a[0],b[1]-a[1]
                t=max(0.,min(1.,((o['x']-a[0])*vx+(o['y']-a[1])*vy)/max(vx*vx+vy*vy,1e-12)))
                if math.hypot(a[0]+t*vx-o['x'],a[1]+t*vy-o['y']) < o['radius'] and a[2]+t*(b[2]-a[2]) < o['height']:
                    self.blocked=True
        if self.blocked:
            self.x,self.y=old_x,old_y
            self.angles=old_angles
            self.mechanics.slip=[0.,0.]
            self.mechanics.slip_total=old_slip_total
            self.mechanics.states=['stick','stick']
            self.mechanics.reason='obstacle'
            from locomotion import normal_loads
            self.mechanics.loads=normal_loads(shape(old_angles[:5]), self.mechanics.settings['mass_kg']*self.mechanics.settings['gravity'])
        # Yaw remains a reduced steering approximation, separate from the sagittal solver.
        center_step = sum(self.mechanics.slip)/2
        self.heading += self.angles[5]*abs(center_step)/.12
        self.nodes=self.geometry()
        self.distance += abs(center_step)
        self.sense()
        self.tick += 1
        return [self.tick, self.tick*DT] + observation + self.targets + self.angles + [self.hp] + list(self.nodes[-1]) + [sum(self.angles[:5])/2, self.heading+self.angles[5],0.] + [self.control_mode,self.mechanics.settings['strategy']] + self.mechanics.loads + self.mechanics.slip + self.mechanics.forces + list(map(int,grips)) + [self.x,self.distance]


class Recorder:
    def __init__(self, path, world):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, 'w', newline='', encoding='utf-8')
        self.file.write('# ' + json.dumps({'schema': 2, 'controller_version': 2, 'angle_convention': 'five relative joints / six links', 'physics': world.mechanics.settings, 'hz': 30, 'observation': 'pre_action_delayed_brightness', 'state': 'post_action', 'config': world.config, 'weights': world.controller.weights, 'initial_tick': world.tick, 'initial_objects': world.objects, 'initial_spawn': world.initial_spawn, 'record_start_pose': {'x':world.x,'y':world.y,'heading_rad':world.heading}}) + '\n')
        self.writer = csv.writer(self.file)
        self.writer.writerow(['tick', 'time_s'] + [f'belly_{i}' for i in range(45)] + [f'head_{i}' for i in range(9)] + [f'target_{i}_rad' for i in range(6)] + [f'actual_{i}_rad' for i in range(6)] + ['hp', 'head_x_m', 'head_y_m', 'head_z_m', 'head_pitch_rad', 'head_yaw_rad', 'head_roll_rad'] + ['control_mode','support_strategy','rear_load_n','front_load_n','rear_slip_m','front_slip_m','rear_force_n','front_force_n','rear_grip','front_grip','rear_x_m','center_path_m'])

    def write(self, row):
        if row is not None:
            self.writer.writerow(row)

    def close(self):
        self.file.close()
