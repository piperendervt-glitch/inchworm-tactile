"""Run a newborn episode without optimization, rewards, or weight updates."""
import argparse
import json
from pathlib import Path
from collections import Counter
from core import World,Recorder,read_config,DT


def observe(config,seconds=30,csv_path=None):
    world=World(config);before=world.controller.weights
    reasons=Counter();recorder=Recorder(csv_path,world) if csv_path else None
    try:
        for _ in range(round(seconds/DT)):
            row=world.step()
            if row is None:break
            reasons[world.mechanics.reason]+=1
            if recorder:recorder.write(row)
    finally:
        if recorder:recorder.close()
    center=world.mechanics.center()
    return dict(controller_version=4,training_enabled=False,training_steps=world.controller.training_steps,
        requested_seconds=seconds,elapsed_seconds=world.tick*DT,controller_seed=world.controller.seed,
        weights_sha256=world.controller.fingerprint,weights_unchanged=before==world.controller.weights,
        initial_spawn=world.initial_spawn,objects=world.objects,displacement_m=[center[i]-world.initial_center[i] for i in range(3)],
        hp=world.hp,hunger=world.hunger,food_events=world.food_events,last_damage_hp=world.damage_hp,
        actual_angles_rad=world.angles,body_center_m=world.mechanics.center(),target_angles_rad=world.targets,support_commands=world.controller.grips,physics_reasons=dict(reasons),config=world.config)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=30.)
    parser.add_argument('--config')
    parser.add_argument('--layout')
    parser.add_argument('--birth-seed',type=int)
    parser.add_argument('--csv')
    parser.add_argument('--output',default='docs/newborn-observation.json')
    args=parser.parse_args()
    if not 0<args.seconds<=3600:parser.error('seconds must be in (0,3600]')
    cfg=read_config(args.config)
    if args.layout:cfg['layout']=json.loads(Path(args.layout).read_text(encoding='utf-8'))
    if args.birth_seed is not None:cfg.setdefault('controller',{})['seed']=args.birth_seed
    result=observe(cfg,args.seconds,args.csv)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('config','objects')},indent=2))
