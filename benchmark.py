"""Reproducible observer-only comparison. Position never enters the controller."""
import argparse
import json
from pathlib import Path
from core import World,read_config


def benchmark(seconds=30):
    rows=[]
    for scene in ('ground','standard'):
        for strategy in ('directional','load_transfer','active_grip'):
            for policy in ('reference','ai'):
                cfg=read_config()
                if scene=='ground':cfg['environment']=[]
                cfg['physics']['strategy']=strategy
                w=World(cfg)
                initial=(w.nodes[0][0]+w.nodes[-1][0])/2
                support={'rear':0,'front':0,'both':0,'neither':0}
                for _ in range(round(seconds*30)):
                    if w.hp <= 0: break
                    w.step(mode=policy)
                    states=w.mechanics.states
                    key='rear' if states==['stick','slip'] else 'front' if states==['slip','stick'] else 'both' if states==['stick','stick'] else 'neither'
                    support[key]+=1
                center=(w.nodes[0][0]+w.nodes[-1][0])/2
                rows.append(dict(scene=scene,strategy=strategy,policy=policy,seconds=seconds,elapsed_s=round(w.tick/30,3),net_x_mm=round((center-initial)*1000,3),path_mm=round(w.distance*1000,3),hp=round(w.hp,3),eaten=sum(bool(o.get('eaten')) for o in w.objects),support_frames=support))
    return {'model':'two-pad-quasistatic-v2','config':read_config(),'results':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=30)
    parser.add_argument('--output',default='docs/benchmark-v2.json')
    args=parser.parse_args()
    if args.seconds<=0:parser.error('seconds must be positive')
    result=benchmark(args.seconds)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    for row in result['results']:print(row)
