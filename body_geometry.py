"""Shared tapered hexagonal hull; pivot remains at +/-45 mm."""
import math
HALF_LENGTH=.045
APOTHEM=.018
RADIUS=APOTHEM/math.cos(math.pi/6)
RING=tuple((math.cos(i*math.pi/3),math.sin(i*math.pi/3)) for i in range(6))
# Wide central belt, narrow tips and 5 mm tip-to-pivot clearance.
# Every vertex fits inside a 28 degree cone from either joint pivot.
SECTIONS=((- .040,.002),(-.005,RADIUS),(.005,RADIUS),(.040,.002))
VERTICES=tuple((x,r*y,r*z) for x,r in SECTIONS for y,z in RING)

def faces():
    result=[([VERTICES[i] for i in reversed(range(6))],(-1,0,0)),
            ([VERTICES[18+i] for i in range(6)],(1,0,0))]
    for k in range(3):
        for i in range(6):
            j=(i+1)%6
            points=[VERTICES[k*6+i],VERTICES[k*6+j],VERTICES[(k+1)*6+j],VERTICES[(k+1)*6+i]]
            a,b,c=points[:3];u=[b[t]-a[t] for t in range(3)];v=[c[t]-a[t] for t in range(3)]
            n=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            length=math.sqrt(sum(t*t for t in n))
            result.append((points,tuple(t/length for t in n)))
    return result
