"""Shared metre-scale hexagonal prism for collision, skin and rendering."""
import math
HALF_LENGTH=.045
APOTHEM=.018
RADIUS=APOTHEM/math.cos(math.pi/6)
RING=tuple((RADIUS*math.cos(i*math.pi/3),RADIUS*math.sin(i*math.pi/3)) for i in range(6))
VERTICES=tuple((x,y,z) for x in (-HALF_LENGTH,HALF_LENGTH) for y,z in RING)

def faces():
    result=[([VERTICES[i] for i in reversed(range(6))],(-1,0,0)),
            ([VERTICES[6+i] for i in range(6)],(1,0,0))]
    for i in range(6):
        j=(i+1)%6;theta=(i+.5)*math.pi/3
        result.append(([VERTICES[i],VERTICES[j],VERTICES[6+j],VERTICES[6+i]],
                       (0,math.cos(theta),math.sin(theta))))
    return result
