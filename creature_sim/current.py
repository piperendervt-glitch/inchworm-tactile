"""The current that carries creatures around the arena. Mirrors CreatureCurrent.cs.

It only moves creatures. The flow comes from a stream function, so it has no
sources and no drains and cannot pile creatures up anywhere; every term is zero
across the arena edges, so it runs along the walls and never presses into them.

A single whirl would leave its centre still and keep everything on its own
ring. Two patterns move the centre about over ``period``, and a four-cell
pattern comes and goes over 1.618 times that. The two never line up the same
way twice, so what floats is passed between inner and outer rings and round
the whole arena.

    u = (x - minX) / W, v = (z - minZ) / H
    p = 2 pi t / period, q = 2 pi t / (1.618 period)
    psi = A [ sin(pi u) sin(pi v)
            + e sin(p) sin(2 pi u) sin(pi v)
            + e cos(p) sin(pi u) sin(2 pi v)
            + m sin(q) sin(2 pi u) sin(2 pi v) ]
    A = speed * min(W, H) / pi
    velocity x = d psi / dz, velocity z = - d psi / dx

See docs/creature-protocol.md 5.2 for worked examples.
"""

import math

DEFAULT_PERIOD = 90.0
DEFAULT_WOBBLE = 0.4
DEFAULT_MIX = 0.5
MIX_PERIOD_RATIO = 1.618


def velocity(x, z, t, bounds, speed, period=DEFAULT_PERIOD, wobble=DEFAULT_WOBBLE, mix=DEFAULT_MIX):
    """The current at (x, z) at time t, as (vx, vz) in m/s."""
    if speed <= 0.0 or not bounds or len(bounds) != 4:
        return 0.0, 0.0
    min_x, min_z, max_x, max_z = bounds
    w = max_x - min_x
    h = max_z - min_z
    if w <= 0.0 or h <= 0.0:
        return 0.0, 0.0
    u = min(1.0, max(0.0, (x - min_x) / w))
    v = min(1.0, max(0.0, (z - min_z) / h))
    a = speed * min(w, h) / math.pi
    pi = math.pi
    p = 2.0 * pi * t / period if period > 0.0 else 0.0
    q = 2.0 * pi * t / (MIX_PERIOD_RATIO * period) if period > 0.0 else 0.0
    s = wobble * math.sin(p)
    c = wobble * math.cos(p)
    f = mix * math.sin(q)
    su, cu = math.sin(pi * u), math.cos(pi * u)
    s2u, c2u = math.sin(2 * pi * u), math.cos(2 * pi * u)
    sv, cv = math.sin(pi * v), math.cos(pi * v)
    s2v, c2v = math.sin(2 * pi * v), math.cos(2 * pi * v)
    dpsi_dx = a * ((pi / w) * cu * sv + s * (2 * pi / w) * c2u * sv
                   + c * (pi / w) * cu * s2v + f * (2 * pi / w) * c2u * s2v)
    dpsi_dz = a * ((pi / h) * su * cv + s * (pi / h) * s2u * cv
                   + c * (2 * pi / h) * su * c2v + f * (2 * pi / h) * s2u * c2v)
    return dpsi_dz, -dpsi_dx


def from_hello(hello):
    """The current a session was recorded with, or None if it had none.

    Sessions recorded before the current existed carry no settings and are
    replayed without one, exactly as they were played.
    """
    settings = ((hello or {}).get('infestation') or {})
    speed = float(settings.get('currentSpeed', 0.0) or 0.0)
    if speed <= 0.0:
        return None
    return dict(speed=speed,
                period=float(settings.get('currentPeriod', DEFAULT_PERIOD)),
                wobble=float(settings.get('currentWobble', DEFAULT_WOBBLE)),
                mix=float(settings.get('currentMix', DEFAULT_MIX)))


def top_speed(settings):
    """A bound on how fast the current can run with these settings."""
    if not settings:
        return 0.0
    return settings['speed'] * (1.0 + 2.0 * settings['wobble'] + 2.0 * settings.get('mix', 0.0))
