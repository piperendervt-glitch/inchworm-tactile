"""What four ears pick up. The same formula runs in Unity (CreatureHearing.cs).

Hearing is a sense the Brain is given, not an instinct. Nothing here turns a
creature toward a sound; the ears only say how loud the world is in front, to
the right, behind and to the left. Whether that is worth acting on is left to
learning.

    loudness  = level / (1 + (distance / REFERENCE) ** 2), zero beyond RANGE
    ear i     points at heading + i * 90 degrees: front, right, back, left
    ear value = sum over sources of loudness * max(0, cos(bearing - ear)),
                clamped to 0..1

Bearing follows the protocol heading: zero is +Z, clockwise from above is
positive, so bearing = atan2(dx, dz). See docs/creature-protocol.md 5.1.
"""

import math

EARS = 4
RANGE = 45.0
REFERENCE = 8.0

PLAYER_BASE = 0.2
PLAYER_MOVE = 0.5
PLAYER_MOVE_SPEED = 12.0
PLAYER_BOOST = 0.2
SHOT_NOISE = 0.6
STRIDER_LEVEL = 0.45
BASTION_LEVEL = 0.3
CHARGING_EXTRA = 0.4


def loudness(level, distance):
    if level <= 0.0 or distance > RANGE:
        return 0.0
    r = distance / REFERENCE
    return level / (1.0 + r * r)


def player_level(speed, boosting=False, firing=0.0):
    """A pilot is loud when moving fast, louder boosting, loudest just after firing."""
    moving = max(0.0, min(1.0, speed / PLAYER_MOVE_SPEED))
    level = PLAYER_BASE + PLAYER_MOVE * moving + (PLAYER_BOOST if boosting else 0.0) + firing
    return max(0.0, min(1.0, level))


def ears(x, z, heading, sounds):
    """Front, right, back, left, each 0..1, for a creature at (x, z) facing heading."""
    out = [0.0] * EARS
    for source in sounds or ():
        pos = source.get('pos')
        level = float(source.get('level', 0.0))
        if not pos or level <= 0.0:
            continue
        dx = float(pos[0]) - x
        dz = float(pos[2]) - z
        distance = math.hypot(dx, dz)
        amount = loudness(level, distance)
        if amount <= 0.0:
            continue
        if distance < 1e-4:
            # Right on top of it: every ear hears it.
            for i in range(EARS):
                out[i] += amount
            continue
        bearing = math.atan2(dx, dz)
        for i in range(EARS):
            gain = math.cos(bearing - (heading + i * math.pi / 2.0))
            if gain > 0.0:
                out[i] += amount * gain
    return [min(1.0, v) for v in out]
