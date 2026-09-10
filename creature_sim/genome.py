"""Heritable differences between individuals, and how they change at division.

Taken from the jellyfish prototype: every gene is a number in a fixed range,
a child's copy is the parent's scaled by a log-normal factor (or shifted by a
little noise, for the thresholds), and founders get two rounds of that so a
fresh population is not a clone. Nothing here is learned; selection is what
happens to the individuals that carry these numbers.

E. coli genes
    speed    fraction of the body's top speed a full run reaches
    metab    basal energy burn, as a multiple of the species norm
    satiety  energy fraction above which the individual divides
    tumble   multiplies the Brain's tumble probability: nervous or steady

Jelly genes
    speed    as above
    metab    as above
    satiety  as above
    pace     ticks between pacemaker firings (a slower pace, a lazier swimmer)
    suck     grazing rate on the plankton, as a multiple of the norm
    noci     skin pressure that counts as a poke and triggers an escape wave
"""

import math
import random

ECOLI_FOUNDER = dict(speed=0.75, metab=1.0, satiety=0.85, tumble=1.0)
JELLY_FOUNDER = dict(speed=0.8, metab=1.0, satiety=0.8, pace=38.0, suck=1.0, noci=0.3)

# (low, high, kind): 'log' genes scale, 'add' genes shift.
RANGES = dict(
    speed=(0.3, 1.0, 'log'),
    metab=(0.5, 2.0, 'log'),
    satiety=(0.6, 0.95, 'add'),
    tumble=(0.4, 2.5, 'log'),
    pace=(24.0, 70.0, 'log'),
    suck=(0.4, 2.0, 'log'),
    noci=(0.05, 0.6, 'add'),
)
LOG_SIGMA = 0.13
ADD_SIGMA = 0.04


def clamp(value, low, high):
    return low if value < low else high if value > high else value


def mutate(genome, rng, sigma=1.0):
    """A child's genome from a parent's. ``sigma`` scales the spread."""
    out = {}
    for key, value in genome.items():
        low, high, kind = RANGES[key]
        if kind == 'log':
            value = value * math.exp(LOG_SIGMA * sigma * rng.gauss(0.0, 1.0))
        else:
            value = value + ADD_SIGMA * sigma * rng.gauss(0.0, 1.0)
        out[key] = clamp(value, low, high)
    return out


def founder(species, rng):
    """A fresh individual with no parent: the norm, spread twice over."""
    base = JELLY_FOUNDER if species == 'jelly' else ECOLI_FOUNDER
    return mutate(mutate(dict(base), rng), rng)


def rounded(genome, places=3):
    return {k: round(v, places) for k, v in genome.items()}


def make_rng(seed, *parts):
    return random.Random(':'.join(str(p) for p in (seed,) + parts))
