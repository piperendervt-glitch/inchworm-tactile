"""Reduced planar two-pad mechanics, not a general rigid-body solver.

Six equal rigid links, five relative pitch joints, two compliant level feet.
Pitch/height of the floating body are solved from the two floor supports.
Normal loads follow static force and moment balance. Coulomb stick/slip is
directional, load-based or controlled by optional explicit gripper commands.
"""
import math

DEFAULT_PHYSICS = dict(strategy='directional', mass_kg=.15, gravity=9.81,
    mu_forward=.16, mu_backward=1.1, mu_isotropic=.5, kinetic_ratio=.8,
    mu_released=.08, mu_gripped=1.2, max_drive_n=.6,
    joint_torque_nm=.035, joint_rate_rad_s=1.8, pad_height_m=.006)


def shape(joints, length=.270):
    points = [(0., 0.)]
    angle = 0.
    for i in range(6):
        if i:
            angle += joints[i - 1]
        points.append((points[-1][0] + length / 6 * math.cos(angle),
                       points[-1][1] + length / 6 * math.sin(angle)))
    # The rear pad has a passive pitch connection; both pads stay level.
    rotation = -math.atan2(points[-1][1], points[-1][0])
    c, s = math.cos(rotation), math.sin(rotation)
    return [(x*c-z*s, x*s+z*c) for x,z in points]


def normal_loads(points, weight):
    span = points[-1][0]
    # Equal link masses, at link centers (not the mean of unequal endpoints).
    com = sum((points[i][0] + points[i+1][0])/2 for i in range(6))/6
    front = max(0., min(weight, weight*com/max(span, 1e-9)))
    return [weight-front, front]


def reference_angles(t, strategy='directional'):
    phase = 2*math.pi*t/3.
    bend = .30*(1-math.cos(phase))
    # Bias the arch towards the intended support during each half-cycle.
    bias = .65*math.sin(phase) if strategy == 'load_transfer' else 0.
    return [-bend*(1+bias*(i-2)/2) for i in range(5)] + [0.]


def reference_grips(t):
    derivative = math.sin(2*math.pi*t/3.)
    return [derivative <= .12, derivative >= -.12]


class PadMechanics:
    def __init__(self, settings=None):
        self.settings = DEFAULT_PHYSICS | (settings or {})
        p = self.settings
        if p['strategy'] not in ('directional', 'load_transfer', 'active_grip'):
            raise ValueError('Unknown support strategy')
        for key in ('mass_kg','gravity','max_drive_n','joint_torque_nm','joint_rate_rad_s'):
            if not math.isfinite(p[key]) or p[key] <= 0:
                raise ValueError(f'{key} must be positive and finite')
        for key in ('mu_forward','mu_backward','mu_isotropic','mu_released','mu_gripped','pad_height_m'):
            if not math.isfinite(p[key]) or p[key] < 0:
                raise ValueError(f'{key} must be nonnegative and finite')
        if not 0 < p['kinetic_ratio'] <= 1:
            raise ValueError('kinetic_ratio must be in (0, 1]')
        self.loads = [p['mass_kg']*p['gravity']/2]*2
        self.slip = [0.,0.]
        self.forces = [0.,0.]
        self.capacity = [0.,0.]
        self.grips = [False,False]
        self.states = ['stick','stick']
        self.reason = 'rest'
        self.slip_total = [0.,0.]

    def advance(self, old, candidate, dt, grips=(False,False)):
        p = self.settings
        before, after = shape(old[:5]), shape(candidate[:5])
        self.loads = normal_loads(after, p['mass_kg']*p['gravity'])
        self.grips = list(grips)
        self.slip = [0.,0.]
        self.forces = [0.,0.]
        self.states = ['stick','stick']
        # A two-pad model cannot accommodate a belly penetrating the floor.
        if min(z for _,z in after) < -1e-7 or after[-1][0] < .07:
            self.reason = 'body-ground limit'
            self.loads = normal_loads(before, p['mass_kg']*p['gravity'])
            self.capacity = [0.,0.]
            return old[:], 0.
        delta = after[-1][0]-before[-1][0]
        directions = [-1 if delta>0 else 1, 1 if delta>0 else -1]
        if p['strategy']=='directional':
            mus = [p['mu_forward'] if d>0 else p['mu_backward'] for d in directions]
        elif p['strategy']=='active_grip':
            mus = [p['mu_gripped'] if grip else p['mu_released'] for grip in grips]
        else:
            mus = [p['mu_isotropic']]*2
        self.capacity = [mu*n for mu,n in zip(mus,self.loads)]
        if abs(delta)<1e-10:
            self.reason = 'rest'
            return candidate[:], 0.
        work_bound = p['joint_torque_nm']*sum(abs(a-b) for a,b in zip(old[:5],candidate[:5]))/abs(delta)
        drive = min(p['max_drive_n'],work_bound)
        low = min(self.capacity)
        if drive < low:
            self.reason = 'force limit'
            self.loads = normal_loads(before,p['mass_kg']*p['gravity'])
            self.capacity = [mu*n for mu,n in zip(mus,self.loads)]
            self.forces = [drive,-drive] if delta>0 else [-drive,drive]
            return old[:],0.
        # Equal resistance: both ends slip symmetrically; no artificial winner.
        if abs(self.capacity[0]-self.capacity[1]) < 1e-9:
            rear_move = -delta/2
            self.states = ['slip','slip']
        elif self.capacity[0] > self.capacity[1]:
            rear_move = 0.
            self.states[1] = 'slip'
        else:
            rear_move = -delta
            self.states[0] = 'slip'
        self.slip = [rear_move,rear_move+delta]
        force = low*p['kinetic_ratio']
        self.forces = [force,-force] if delta>0 else [-force,force]
        self.reason = 'extend' if delta>0 else 'contract'
        self.slip_total = [total+abs(slip) for total,slip in zip(self.slip_total,self.slip)]
        return candidate[:],rear_move
