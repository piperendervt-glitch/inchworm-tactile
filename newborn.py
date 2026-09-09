"""Frozen, randomly initialized local recurrent network. No gait or optimizer."""
from dataclasses import dataclass
import hashlib
import json
import math
import random


@dataclass(frozen=True)
class Observation:
    belly: tuple
    head: tuple
    joint_touch: tuple
    support_touch: tuple
    hp: float
    damage_hp: float
    hunger: float
    food_relief: float

    def flatten(self):
        return list(self.belly+self.head+self.joint_touch+self.support_touch)+( [self.hp,self.damage_hp,self.hunger,self.food_relief])


class NewbornController:
    VERSION=5
    INPUTS=49  # 9 surface + 9 head + 6 joint + 9 support + 4 internal + 12 recurrent
    HIDDEN=4

    def __init__(self,seed=17):
        if not isinstance(seed,int) or not 0<=seed<=2147483647:
            raise ValueError('controller.seed must be an integer in [0, 2147483647]')
        self.seed=seed
        rng=random.Random(seed)
        self.input_weights=tuple(tuple(rng.gauss(0,.35/math.sqrt(self.INPUTS)) for _ in range(self.INPUTS)) for _ in range(4))
        self.output_weights=tuple(tuple(rng.gauss(0,.35/2) for _ in range(4)) for _ in range(4))
        self.weights=tuple(v for row in self.input_weights+self.output_weights for v in row)
        self.fingerprint=hashlib.sha256(json.dumps(self.weights).encode()).hexdigest()
        self.state=[[0.]*4 for _ in range(3)]
        self.features=[[0.]*4 for _ in range(3)]
        self.local_inputs=[[0.]*self.INPUTS for _ in range(3)]
        self.grips=[0.,0.]
        self.outputs=[[0.,0.,0.,0.] for _ in range(3)]
        self.training_steps=0

    def step(self,obs):
        if not isinstance(obs,Observation):raise TypeError('Observation required')
        if tuple(map(len,(obs.belly,obs.head,obs.joint_touch,obs.support_touch)))!=(27,9,12,18):
            raise ValueError('Expected 27 belly, 9 head, 12 joint-touch and 18 support-touch values')
        if not all(math.isfinite(v) and 0<=v<=1 for v in obs.flatten()):
            raise ValueError('All policy inputs must be normalized to [0,1]')
        old=self.state;new=[]
        for i in range(3):
            belly=list(obs.belly[i*9:(i+1)*9])
            head=list(obs.head) if i==2 else [0.]*9
            joint=list(obs.joint_touch[:6] if i==0 else obs.joint_touch[6:]) if i in (0,2) else [0.]*6
            support=list(obs.support_touch[:9] if i==0 else obs.support_touch[9:]) if i in (0,2) else [0.]*9
            internal=[obs.hp,obs.damage_hp,obs.hunger,obs.food_relief]
            inputs=belly+head+joint+support+internal+old[i]+(old[i-1] if i else [0.]*4)+(old[i+1] if i<2 else [0.]*4)
            self.local_inputs[i]=inputs
            self.features[i]=[sum(belly)/9,joint[0],joint[1],sum(support)/9]
            hidden=[math.tanh(sum(w*v for w,v in zip(row,inputs))) for row in self.input_weights]
            new.append(hidden)
        self.state=new
        for i,hidden in enumerate(new):
            raw=[sum(w*v for w,v in zip(row,hidden)) for row in self.output_weights]
            self.outputs[i]=[.9*math.tanh(raw[0]),max(0.,math.tanh(raw[1])),.9*math.tanh(raw[2]),.9*math.tanh(raw[3])]
        # Terminal sensory modules drive joint 1/2 XYZ and end-segment support.
        # Middle modules exchange recurrent state; no gait or clock.
        self.grips=[self.outputs[0][1],self.outputs[2][1]]
        return [self.outputs[i][k] for i in (0,2) for k in (0,2,3)]
