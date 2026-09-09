"""Independent smooth motor babbling; no gait, rewards or network updates."""
import math
import random
from body_limits import ANGLE_LIMITS

class MotorExploration:
    def __init__(self, seed, settings=None):
        self.settings=dict(enabled=True,weight=.9,amplitude=1.,min_seconds=1.5,max_seconds=4.,max_rate_rad_s=1.2)
        self.settings.update(settings or {})
        s=self.settings
        if type(s['enabled']) is not bool:raise ValueError('exploration.enabled must be boolean')
        for k in ('weight','amplitude'):
            if not math.isfinite(s[k]) or not 0<=s[k]<=1:raise ValueError('Invalid exploration '+k)
        for k in ('min_seconds','max_seconds','max_rate_rad_s'):
            if not math.isfinite(s[k]) or s[k]<=0:raise ValueError('Invalid exploration '+k)
        if s['max_seconds']<s['min_seconds']:raise ValueError('Invalid exploration duration range')
        self.rngs=[random.Random(seed*1009+7919*(i+1)) for i in range(6)]
        self.values=[0.]*6;self.starts=[0.]*6;self.goals=[0.]*6
        self.elapsed=[0.]*6;self.durations=[0.]*6
        self.steps=0
        for i in range(6):self.choose(i)

    def choose(self,i):
        s=self.settings;rng=self.rngs[i]
        self.starts[i]=self.values[i]
        self.goals[i]=rng.uniform(-1,1)*ANGLE_LIMITS[i]*s['amplitude']
        # Quintic smoothstep has a maximum derivative of 1.875.
        self.durations[i]=max(rng.uniform(s['min_seconds'],s['max_seconds']),
            1.875*abs(self.goals[i]-self.starts[i])/s['max_rate_rad_s'])
        self.elapsed[i]=0.

    def step(self,dt):
        if not math.isfinite(dt) or dt<=0:raise ValueError('Positive dt required')
        if not self.settings['enabled']:return self.values[:]
        for i in range(6):
            remaining=dt
            while remaining>1e-12:
                advance=min(remaining,self.durations[i]-self.elapsed[i])
                self.elapsed[i]+=advance;remaining-=advance
                t=min(1.,self.elapsed[i]/self.durations[i])
                smooth=t*t*t*(10+t*(-15+6*t))
                self.values[i]=self.starts[i]+(self.goals[i]-self.starts[i])*smooth
                if self.elapsed[i]>=self.durations[i]-1e-12:
                    self.values[i]=self.goals[i];self.choose(i)
        self.steps+=1
        return self.values[:]

    def mix(self,network,dt):
        self.step(dt)
        w=self.settings['weight'] if self.settings['enabled'] else 0.
        return [(1-w)*v+w*e for v,e in zip(network,self.values)]
