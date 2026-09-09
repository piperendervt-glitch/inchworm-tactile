"""Deterministic environment generation, independent of tactile sensor noise."""
import copy
import math
import random

KINDS=('obstacle','harm','food')
DEFAULTS={'obstacle':(.07,.065),'harm':(.08,.005),'food':(.055,.04)}


def finite(value,name):
    value=float(value)
    if not math.isfinite(value):raise ValueError(f'{name}: 有限の数値を指定してください')
    return value


def integer(value,name,low=0,high=100):
    number=finite(value,name)
    if number!=int(number) or not low<=number<=high:raise ValueError(f'{name}: {low}〜{high}の整数を指定してください')
    return int(number)


def layout_from_config(config):
    if 'layout' in config:return copy.deepcopy(config['layout'])
    result={'seed':config.get('seed',7),'bounds':[-1.,2.,-1.,1.],
            'spawn':{'x':0.,'y':0.,'heading_deg':0.,'randomize':False},'groups':{}}
    result['spawn'].update(config.get('spawn',{}))
    for kind in KINDS:
        positions=[{k:o[k] for k in ('x','y','radius','height')} for o in config['environment'] if o['kind']==kind]
        radius,height=DEFAULTS[kind]
        result['groups'][kind]={'count':len(positions),'random_count':False,'count_min':0,'count_max':max(3,len(positions)),
            'random_positions':False,'radius':radius,'height':height,'positions':positions}
    return result


def body_distance(x,y,spawn):
    angle=math.radians(spawn['heading_deg'])
    vx,vy=.183*math.cos(angle),.183*math.sin(angle)
    t=max(0.,min(1.,((x-spawn['x'])*vx+(y-spawn['y'])*vy)/(.183**2)))
    return math.hypot(x-spawn['x']-t*vx,y-spawn['y']-t*vy)


def resolve_layout(layout):
    layout=copy.deepcopy(layout)
    seed=integer(layout.get('seed',7),'配置seed',0,2147483647)
    bounds=[finite(v,'配置範囲') for v in layout['bounds']]
    if len(bounds)!=4:raise ValueError('配置範囲はX最小・最大、Y最小・最大の4値です')
    xmin,xmax,ymin,ymax=bounds
    if xmin>=xmax or ymin>=ymax:raise ValueError('配置範囲は最小 < 最大にしてください')
    spawn={k:finite(layout['spawn'].get(k,0.),k) for k in ('x','y','heading_deg')}
    groups={};counts={};manual=[]
    for kind in KINDS:
        g=layout['groups'][kind];groups[kind]=g
        count=integer(g['count'],kind+' 個数')
        low=integer(g.get('count_min',0),kind+' 最小個数');high=integer(g.get('count_max',count),kind+' 最大個数')
        if low>high:raise ValueError(kind+': 最小個数を最大個数以下にしてください')
        if g.get('random_count'):count=random.Random(f'{seed}:{kind}:count').randint(low,high)
        counts[kind]=count
        radius=finite(g['radius'],'半径');height=finite(g['height'],'高さ')
        if radius<=0 or height<=0:raise ValueError('半径と高さは正の値にしてください')
        if not g.get('random_positions'):
            if len(g['positions'])<count:raise ValueError(f'{kind}: 座標が{count}行必要です。「個数分の座標を補充」を使えます')
            for pos in g['positions'][:count]:
                r=finite(pos.get('radius',radius),'半径');h=finite(pos.get('height',height),'高さ')
                if r<=0 or h<=0:raise ValueError('半径と高さは正の値にしてください')
                manual.append(dict(kind=kind,x=finite(pos['x'],'X'),y=finite(pos['y'],'Y'),radius=r,height=h))
    if layout['spawn'].get('randomize'):
        rng=random.Random(f'{seed}:spawn')
        angle=math.radians(spawn['heading_deg'])
        for _ in range(2000):
            spawn['x']=rng.uniform(xmin,xmax);spawn['y']=rng.uniform(ymin,ymax)
            hx,hy=spawn['x']+.183*math.cos(angle),spawn['y']+.183*math.sin(angle)
            if not (xmin+.03<=min(spawn['x'],hx) and max(spawn['x'],hx)<=xmax-.03 and ymin+.03<=min(spawn['y'],hy) and max(spawn['y'],hy)<=ymax-.03):continue
            if all(body_distance(o['x'],o['y'],spawn)>o['radius']+.03 for o in manual):break
        else:raise ValueError('AIを配置できません。範囲を広げるか固定物体を減らしてください')
    objects=manual[:]
    for kind in KINDS:
        g=groups[kind]
        if not g.get('random_positions'):continue
        rng=random.Random(f'{seed}:{kind}:positions')
        r=float(g['radius']);h=float(g['height'])
        if counts[kind] and (xmax-xmin<2*r or ymax-ymin<2*r):raise ValueError(kind+': 配置範囲が半径に対して狭すぎます')
        for _ in range(counts[kind]):
            for attempt in range(2000):
                x=rng.uniform(xmin+r,xmax-r);y=rng.uniform(ymin+r,ymax-r)
                if body_distance(x,y,spawn)<=r+.03:continue
                if any(math.hypot(x-o['x'],y-o['y'])<=r+o['radius']+.01 for o in objects):continue
                objects.append(dict(kind=kind,x=x,y=y,radius=r,height=h));break
            else:raise ValueError(kind+': 重ならずに配置できません。範囲を広げるか個数を減らしてください')
    return spawn,objects
