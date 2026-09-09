"""Planar run-and-tumble baseline. No vision, navigation rules or learning."""
import copy
import hashlib
import json
import math
import random

DT=1/30
DEFAULT=dict(bounds=[-1,1,-.7,.7],layout_seed=7,spawn=[0,0,0],random_spawn=False,
             obstacle_count=5,harm_count=3,food_count=8,body_radius=.018,
             speed=.12,drag=5.,decision_seconds=.25,concentration_scale=.25,
             initial_energy=100.,basal_cost=.1,motion_cost=.2,damage_per_s=8.,
             food_per_s=12.,food_quantity=30.,sensor_noise=.005)

def config(values=None):
    c=copy.deepcopy(DEFAULT);c.update(values or {})
    for k in ('body_radius','speed','drag','decision_seconds','concentration_scale','food_quantity'):
        if not math.isfinite(c[k]) or c[k]<=0:raise ValueError(k+' must be positive')
    for k in ('initial_energy','basal_cost','motion_cost','damage_per_s','food_per_s','sensor_noise'):
        if not math.isfinite(c[k]) or c[k]<0:raise ValueError(k+' must be nonnegative')
    for k in ('layout_seed','obstacle_count','harm_count','food_count'):
        if type(c[k]) is not int or not 0<=c[k]<= (2147483647 if k=='layout_seed' else 100):raise ValueError('Invalid '+k)
    if not 0<c['initial_energy']<=100:raise ValueError('initial_energy must be in (0,100]')
    b=c['bounds'];r=c['body_radius']
    if len(b)!=4 or not all(math.isfinite(v) for v in b) or b[1]-b[0]<=2*r or b[3]-b[2]<=2*r:raise ValueError('Invalid bounds')
    if len(c['spawn'])!=3 or not all(math.isfinite(v) for v in c['spawn']):raise ValueError('Invalid spawn')
    if type(c['random_spawn']) is not bool:raise ValueError('random_spawn must be boolean')
    return c

class Policy:
    """Frozen 6 sensory/action inputs + four recurrent states -> tumble probability."""
    def __init__(self,seed):
        rng=random.Random(f'policy:{seed}')
        self.w=[[rng.gauss(0,.4) for _ in range(10)] for _ in range(4)]
        self.out=[rng.gauss(0,.5) for _ in range(4)]
        self.state=[0.]*4;self.inputs=[0.]*6;self.probability=.5
        self.fingerprint=hashlib.sha256(json.dumps([self.w,self.out]).encode()).hexdigest()
    def step(self,inputs):
        self.inputs=list(inputs);v=list(inputs)+self.state
        self.state=[math.tanh(sum(a*b for a,b in zip(row,v))) for row in self.w]
        raw=sum(a*b for a,b in zip(self.out,self.state))
        self.probability=1/(1+math.exp(-raw))
        return self.probability

class World:
    def __init__(self,seed=0,settings=None):
        if type(seed) is not int or not 0<=seed<=2147483647:raise ValueError('Invalid individual seed')
        self.c=config(settings);self.seed=seed;self.policy=Policy(seed)
        self.actions=random.Random(f'action:{seed}');self.noise=random.Random(f'noise:{seed}')
        rng=random.Random(self.c['layout_seed']);xmin,xmax,ymin,ymax=self.c['bounds'];r=self.c['body_radius']
        self.x,self.y,degrees=self.c['spawn'];self.heading=math.radians(degrees)
        if self.c['random_spawn']:
            self.x=rng.uniform(xmin+r,xmax-r);self.y=rng.uniform(ymin+r,ymax-r);self.heading=rng.uniform(-math.pi,math.pi)
        if not xmin+r<=self.x<=xmax-r or not ymin+r<=self.y<=ymax-r:raise ValueError('Spawn outside bounds')
        self.objects=[]
        if 'objects' in self.c:
            for o in self.c['objects']:
                if o.get('kind') not in ('obstacle','harm','food') or any(not math.isfinite(o[k]) for k in ('x','y','radius')) or o['radius']<=0:raise ValueError('Invalid object')
                self.objects.append(copy.deepcopy(o))
        else:
            for kind,radius in (('obstacle',.085),('harm',.09),('food',.045)):
                for _ in range(self.c[kind+'_count']):
                    for attempt in range(5000):
                        x=rng.uniform(xmin+radius,xmax-radius);y=rng.uniform(ymin+radius,ymax-radius)
                        if math.hypot(x-self.x,y-self.y)<radius+r+.06:continue
                        if any(math.hypot(x-o['x'],y-o['y'])<radius+o['radius']+.025 for o in self.objects):continue
                        self.objects.append(dict(kind=kind,x=x,y=y,radius=radius));break
                    else:raise ValueError('Cannot place objects; reduce counts or enlarge bounds')
        for o in self.objects:
            if o['kind']=='obstacle' and math.hypot(self.x-o['x'],self.y-o['y'])<r+o['radius']:raise ValueError('Spawn intersects obstacle')
            if o['kind']=='food':o['remaining']=self.c['food_quantity']
        self.initial_objects=copy.deepcopy(self.objects);self.initial_pose=[self.x,self.y,self.heading]
        self.tick=0;self.energy=self.c['initial_energy'];self.damage=0.;self.food=0.
        self.damage_total=0.;self.food_total=0.;self.path=0.;self.contact_seconds=0.;self.contact=0.
        self.speed=0.;self.action='run';self.turn_left=0.;self.turn_rate=0.;self.decision_left=0.
        self.turns=0;self.death_time=None;self.concentration=self.sense_concentration();self.last_inputs=[0.]*6

    def sense_concentration(self):
        total=sum(math.exp(-math.hypot(self.x-o['x'],self.y-o['y'])/self.c['concentration_scale'])*o['remaining']/self.c['food_quantity'] for o in self.objects if o['kind']=='food')
        return max(0.,min(1.,1-math.exp(-total)+self.noise.gauss(0,self.c['sensor_noise'])))

    def step(self):
        self.last_inputs=[self.contact,self.concentration,self.energy/100,min(1.,self.damage/100),min(1.,self.food/100),float(self.action=='tumble')]
        self.damage=0.;self.food=0.;self.decision_made=False
        if self.energy<=0:
            self.tick+=1;self.action='dead';self.speed=0.;return self.row()
        if self.turn_left<=0 and self.decision_left<=0:
            self.decision_made=True
            probability=self.policy.step(self.last_inputs)
            if self.actions.random()<probability:
                self.action='tumble';self.turn_left=self.actions.uniform(.25,.8)
                self.turn_rate=self.actions.uniform(-math.pi,math.pi)/self.turn_left;self.turns+=1
            else:self.action='run'
            self.decision_left=self.c['decision_seconds']
        if self.turn_left>0:
            h=min(DT,self.turn_left);self.heading+=self.turn_rate*h;self.turn_left-=h
            desired=0.
        else:
            self.action='run';desired=self.c['speed'];self.decision_left-=DT
        self.heading=(self.heading+math.pi)%(2*math.pi)-math.pi
        self.speed+=(desired-self.speed)*(1-math.exp(-self.c['drag']*DT))
        dx=self.speed*DT*math.cos(self.heading);dy=self.speed*DT*math.sin(self.heading)
        r=self.c['body_radius'];xmin,xmax,ymin,ymax=self.c['bounds']
        count=max(1,math.ceil(math.hypot(dx,dy)/(r*.25)));self.contact=0.
        for _ in range(count):
            x=self.x+dx/count;y=self.y+dy/count
            collision=not (xmin+r<=x<=xmax-r and ymin+r<=y<=ymax-r) or any(o['kind']=='obstacle' and math.hypot(x-o['x'],y-o['y'])<r+o['radius'] for o in self.objects)
            if collision:self.contact=1.;self.speed=0.;break
            self.path+=math.hypot(x-self.x,y-self.y);self.x,self.y=x,y
        if self.contact:self.contact_seconds+=DT
        harmful=False
        for o in self.objects:
            if math.hypot(self.x-o['x'],self.y-o['y'])>r+o['radius']:continue
            if o['kind']=='harm':harmful=True
            if o['kind']=='food':
                amount=min(o['remaining'],self.c['food_per_s']*DT,100-self.energy)
                o['remaining']-=amount;self.food+=amount;self.energy+=amount
        self.damage=min(self.energy,self.c['damage_per_s']*DT if harmful else 0.)
        self.energy=max(0.,self.energy-self.damage-DT*(self.c['basal_cost']+self.c['motion_cost']*(self.speed/self.c['speed'])))
        self.damage_total+=self.damage;self.food_total+=self.food
        self.tick+=1
        if self.energy<=0:self.death_time=self.tick*DT
        self.concentration=self.sense_concentration()
        return self.row()

    def row(self):
        return dict(tick=self.tick,time_s=self.tick*DT,x=self.x,y=self.y,heading=self.heading,
                    decision_made=getattr(self,'decision_made',False),action=self.action,contact=self.contact,concentration=self.concentration,energy=self.energy,
                    damage=self.damage,food=self.food,tumble_probability=self.policy.probability,
                    **{f'input_{i}':v for i,v in enumerate(self.last_inputs)},
                    **{f'h{i}':v for i,v in enumerate(self.policy.state)})
    def summary(self):
        return dict(seed=self.seed,elapsed_s=self.tick*DT,energy=self.energy,food=self.food_total,
                    damage=self.damage_total,path_m=self.path,contact_s=self.contact_seconds,
                    tumbles=self.turns,death_time=self.death_time,training_enabled=False,training_steps=0,
                    weights_sha256=self.policy.fingerprint)
