"""30 Hz tactile-only policy coupled to substepped Bullet rigid-body dynamics."""
import copy
import csv
import json
import math
import random
from pathlib import Path
from locomotion import RigidMechanics
from newborn import NewbornController, Observation
from layout import resolve_layout,finite

DT = 1 / 30


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


class World:
    def __init__(self, config=None):
        self.config = copy.deepcopy(config or read_config())
        if self.config.get('controller',{}).get('training_enabled',False) is not False:
            raise ValueError('Training cannot be enabled on the newborn branch')
        sensor=self.config['sensor']
        for key,default in (('joint_preload',.05),('joint_gain',.8),('support_load_scale_n',.9)):
            value=sensor.get(key,default)
            if not math.isfinite(value) or value<0 or (key=='support_load_scale_n' and value==0):
                raise ValueError('Invalid tactile response parameter: '+key)
        self.rng = random.Random(self.config['seed'])
        if 'layout' in self.config:
            spawn,self.objects=resolve_layout(self.config['layout'])
        else:
            spawn={k:finite(self.config.get('spawn',{}).get(k,0.),k) for k in ('x','y','heading_deg')}
            self.objects=copy.deepcopy(self.config['environment'])
        self.initial_spawn=copy.deepcopy(spawn)
        self.controller = NewbornController(self.config.get('controller',{}).get('seed',17))
        self.mechanics = RigidMechanics(self.config.get('physics'), spawn, self.objects)
        self.control_mode = 'newborn'
        self.distance = 0.
        self.blocked = False
        self.tick = 0
        self.physiology={'initial_hp':100.,'initial_hunger':20.,'hunger_per_s':.5,'basal_hp_per_s':.2,'starvation_hp_per_s':2.,'damage_hp_per_s':12.,'food_relief':40.,'feeding_seconds':1.} | self.config.get('physiology',{})
        if any(not math.isfinite(v) or v<0 for v in self.physiology.values()):
            raise ValueError('Physiology parameters must be finite and nonnegative')
        if not 0<=self.physiology['initial_hp']<=100 or not 0<=self.physiology['initial_hunger']<=100 or self.physiology['feeding_seconds']<=0:
            raise ValueError('Invalid initial HP, hunger, or feeding duration')
        self.hp=self.physiology['initial_hp']
        self.hunger=self.physiology['initial_hunger']
        self.damage_hp=0.
        self.food_relief=0.
        self.food_events=0
        self.x=spawn['x'];self.y=spawn['y'];self.heading=math.radians(spawn['heading_deg'])
        self.angles = [0.] * 6
        self.targets = [0.] * 6
        self.belly = [self.config['sensor']['baseline']] * 27
        self.head = [self.config['sensor']['baseline']] * 9
        self.joint_touch=[self.config['sensor']['baseline']]*12
        self.support_touch=[self.config['sensor']['baseline']]*18
        self.queue = [(self.belly[:],self.head[:],self.joint_touch[:],self.support_touch[:]) for _ in range(self.config['sensor']['delay_frames'])]
        self.food_hold = {}
        self.nodes = self.geometry()
        self.initial_center=self.mechanics.center()

    def geometry(self):
        return self.mechanics.geometry()

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
        for x,y,z in self.mechanics.surface_points():
            kind, _, height = self.material_at(x,y)
            # Surface receptors approximate compliant skin, not visual ranging.
            indentation=max(0.,(.002+height-z)/.008)
            harmful |= kind=='harm' and indentation>0
            belly.append(self.brightness(indentation,kind,x,y))
        hx,hy,hz=self.mechanics.head_position()
        facing=self.heading
        food_contacts = set()
        for row in range(3):
            for col in range(3):
                x,y,z=self.mechanics.point(self.mechanics.segments[2],(.045,(col-1)*.009,(row-1)*.009))
                kind, idx, height = self.material_at(x,y)
                indentation = max(0., (height + .01 - z) / .025) if idx >= 0 else 0.
                harmful |= kind == 'harm' and indentation > 0
                if kind == 'food' and indentation > 0:
                    food_contacts.add(idx)
                head.append(self.brightness(indentation, kind, x, y))
        support=[]
        for pad,node_index in enumerate((0,-1)):
            px,py,_=self.mechanics.feet()[pad]
            for row in range(3):
                for col in range(3):
                    u=(row-1)*pitch;v=(col-1)*pitch
                    x=px+u*math.cos(self.heading)-v*math.sin(self.heading)
                    y=py+u*math.sin(self.heading)+v*math.cos(self.heading)
                    kind,_,_=self.material_at(x,y)
                    load=self.mechanics.loads[pad]/self.config['sensor'].get('support_load_scale_n',.9)
                    harmful |= kind=='harm' and load>0
                    support.append(self.brightness(load,kind,x,y))
        joint=[]
        for angle in self.angles:
            for direction in (1,-1):
                indentation=self.config['sensor'].get('joint_preload',.05)+self.config['sensor'].get('joint_gain',.8)*max(0.,direction*angle)/.9
                joint.append(self.brightness(indentation,'ground',0.,0.))
        self.food_relief=0.
        self.hunger=min(100.,self.hunger+self.physiology['hunger_per_s']*DT)
        for idx in range(len(self.objects)):
            self.food_hold[idx] = self.food_hold.get(idx,0)+DT if idx in food_contacts else 0.
            if self.food_hold[idx]>=self.physiology['feeding_seconds'] and not self.objects[idx].get('eaten'):
                self.objects[idx]['eaten']=True
                relief=min(self.hunger,self.physiology['food_relief'])
                self.hunger-=relief
                self.food_relief+=relief
                self.food_events+=1
        basal=DT*(self.physiology['basal_hp_per_s']+(self.physiology['starvation_hp_per_s'] if self.hunger>=100 else 0.))
        requested_damage=DT*self.physiology['damage_hp_per_s'] if harmful else 0.
        self.damage_hp=min(self.hp,requested_damage)
        self.hp=max(0.,self.hp-self.damage_hp-basal)
        self.queue.append((belly,head,joint,support))
        self.belly,self.head,self.joint_touch,self.support_touch=self.queue.pop(0)

    def observation(self):
        return Observation(tuple(self.belly),tuple(self.head),tuple(self.joint_touch),tuple(self.support_touch),
                           self.hp/100,min(1.,self.damage_hp/100),self.hunger/100,min(1.,self.food_relief/100))

    def step(self, manual=None, mode='newborn', manual_grips=(0.,0.)):
        if mode != 'newborn':raise ValueError('Only the untrained newborn policy is available')
        if self.hp<=0:return None
        observation=self.observation()
        self.control_mode='manual' if manual is not None else 'newborn'
        if manual is not None:
            self.targets=list(manual)
            grips=list(manual_grips)
        else:
            self.targets=self.controller.step(observation)
            grips=self.controller.grips[:]
        if len(self.targets) != 6 or not all(math.isfinite(v) for v in self.targets):
            raise ValueError('Six finite target angles required')
        self.targets = [max(-.9,min(.9,v)) for v in self.targets]
        before=self.mechanics.center()
        self.mechanics.advance(self.targets,DT,grips)
        self.angles=self.mechanics.angles()
        self.nodes=self.geometry()
        self.x,self.y,_=self.mechanics.feet()[0]
        self.heading=self.mechanics.heading()
        self.blocked=self.mechanics.obstacle_contact
        self.distance+=math.dist(before,self.mechanics.center())
        self.sense()
        self.tick += 1
        return [self.tick,self.tick*DT]+observation.flatten()+self.targets+self.angles+[self.hp,self.hunger,self.damage_hp,self.food_relief,self.food_events]+list(self.mechanics.head_position())+list(self.mechanics.orientation())+[self.control_mode,self.mechanics.settings['strategy']]+self.mechanics.loads+self.mechanics.slip+self.mechanics.forces+list(grips)+[self.x,self.distance]



class Recorder:
    def __init__(self, path, world):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, 'w', newline='', encoding='utf-8')
        self.file.write('# '+json.dumps({'schema':5,'controller_version':5,'training_enabled':False,'training_steps':0,'controller_seed':world.controller.seed,'weights_sha256':world.controller.fingerprint,'initial_weights':world.controller.weights,'angle_convention':'joint 1 XYZ then joint 2 XYZ radians; three capsules lying on floor','physics':world.mechanics.settings,'physiology':world.physiology,'hz':30,'observation':'pre_action; tactile delayed, internal state from preceding step; all normalized 0..1','state':'post_action','config':world.config,'initial_tick':world.tick,'initial_objects':world.objects,'initial_spawn':world.initial_spawn,'record_start_pose':{'x':world.x,'y':world.y,'heading_rad':world.heading}})+'\n')
        self.writer=csv.writer(self.file)
        self.writer.writerow(['tick','time_s']+[f'belly_{i}' for i in range(27)]+[f'head_{i}' for i in range(9)]+[f'joint_touch_{i}' for i in range(12)]+[f'support_touch_{i}' for i in range(18)]+['input_hp','input_damage_hp','input_hunger','input_food_relief']+[f'target_{i}_rad' for i in range(6)]+[f'actual_{i}_rad' for i in range(6)]+['hp','hunger','damage_hp','food_relief','food_events','head_x_m','head_y_m','head_z_m','head_pitch_rad','head_yaw_rad','head_roll_rad','control_mode','support_strategy','rear_load_n','front_load_n','rear_foot_travel_m','front_foot_travel_m','rear_force_n','front_force_n','rear_grip','front_grip','rear_x_m','center_path_m'])

    def write(self, row):
        if row is not None:
            self.writer.writerow(row)

    def close(self):
        self.file.close()
