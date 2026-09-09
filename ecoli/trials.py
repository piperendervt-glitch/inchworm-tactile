"""Fixed-duration sequential seed trials; standard-library only."""
import argparse
import csv
import json
import math
from pathlib import Path
from .core import World,DT

def seeds_from_text(text):
    seeds=[int(v.strip()) for v in text.split(',') if v.strip()]
    if not seeds or len(seeds)>1000 or len(set(seeds))!=len(seeds) or any(not 0<=s<=2147483647 for s in seeds):raise ValueError('Use 1-1000 unique nonnegative seeds, comma separated')
    return seeds

class Trials:
    def __init__(self,seeds,seconds,settings,output):
        self.seeds=seeds_from_text(','.join(map(str,seeds)))
        if not math.isfinite(seconds) or not DT<=seconds<=3600:raise ValueError('Duration must be 1/30 to 3600 seconds')
        self.steps=round(seconds/DT);self.seconds=self.steps*DT;self.settings=settings
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        if any(self.output.iterdir()):raise ValueError('Choose an empty output folder to preserve existing trials')
        self.index=-1;self.world=None;self.file=None;self.done=False;self.results=[]
        self.next()
    def next(self):
        self.index+=1
        if self.index==len(self.seeds):self.done=True;return
        self.world=World(self.seeds[self.index],self.settings)
        self.file=(self.output/f'seed-{self.world.seed}.csv').open('w',newline='',encoding='utf-8')
        self.writer=csv.DictWriter(self.file,fieldnames=self.world.row().keys());self.writer.writeheader()
        metadata=dict(schema=1,seed=self.world.seed,duration_s=self.seconds,config=self.world.c,
                      initial_pose=self.world.initial_pose,objects=self.world.initial_objects,
                      policy_weights=[self.world.policy.w,self.world.policy.out],training_enabled=False,
                      rng_streams=['policy:seed','action:seed','noise:seed'],input_order=['contact','concentration','energy','damage','food','previous_tumble'])
        (self.output/f'seed-{self.world.seed}.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    def step(self):
        if self.done:return
        self.writer.writerow(self.world.step())
        if self.world.tick>=self.steps:
            self.file.close();self.file=None;self.results.append(self.world.summary());self.save_summary();self.next()
    def save_summary(self):
        (self.output/'summary.json').write_text(json.dumps(dict(duration_s=self.seconds,planned_seeds=self.seeds,completed=self.results),indent=2),encoding='utf-8')
        with (self.output/'summary.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=self.results[0].keys());writer.writeheader();writer.writerows(self.results)
    def close(self):
        if self.file:
            self.file.close();self.file=None
            (self.output/'interrupted.json').write_text(json.dumps(self.world.summary(),indent=2),encoding='utf-8')
        self.done=True

def main():
    p=argparse.ArgumentParser();p.add_argument('--seeds',default='0,1,2,3,4,5,6,7,8,9');p.add_argument('--seconds',type=float,default=60)
    p.add_argument('--environment',choices=['food_only','mixed']);p.add_argument('--layout-seed',type=int);p.add_argument('--config');p.add_argument('--output',required=True)
    a=p.parse_args();settings=json.loads(Path(a.config).read_text(encoding='utf-8')) if a.config else {}
    if a.environment:
        if not a.config:settings=json.loads(Path(__file__).with_name(a.environment+'.json').read_text(encoding='utf-8'))
        settings['environment']=a.environment
    if a.layout_seed is not None:settings['layout_seed']=a.layout_seed
    try:t=Trials(seeds_from_text(a.seeds),a.seconds,settings,a.output)
    except ValueError as error:p.error(str(error))
    try:
        while not t.done:t.step()
    finally:t.close()
    print(json.dumps(t.results,indent=2))

if __name__=='__main__':main()
