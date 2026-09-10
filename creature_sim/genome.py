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

# The reflex genes: with the brain switched off ("genes" policy) these alone
# decide when an E. coli tumbles. Signed, so a line can come to seek a sound
# or to flee it; zero means the sense is ignored.
#   base     tumble probability per decision with nothing sensed
#   hearing  sound ahead minus behind: positive tumbles more (turns away)
#   food     food trace ahead minus behind: negative keeps running toward it
#   fear     damage and death traces underfoot
#   touch    pressure on the front of the skin (a wall ahead)
#   noise    randomness added to every decision
ECOLI_REFLEX = dict(base=0.3, hearing=0.0, food=0.0, fear=0.0, touch=1.0, noise=0.5)
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
    base=(0.02, 0.9, 'add'),
    hearing=(-4.0, 4.0, 'wide'),
    food=(-4.0, 4.0, 'wide'),
    fear=(-4.0, 4.0, 'wide'),
    touch=(-2.0, 4.0, 'wide'),
    noise=(0.0, 2.0, 'add'),
)
LOG_SIGMA = 0.13
ADD_SIGMA = 0.04
# The signed reflex genes span a range of eight, so they step further.
WIDE_SIGMA = 0.3


def clamp(value, low, high):
    return low if value < low else high if value > high else value


def mutate(genome, rng, sigma=1.0):
    """A child's genome from a parent's. ``sigma`` scales the spread."""
    out = {}
    for key, value in genome.items():
        low, high, kind = RANGES[key]
        if kind == 'log':
            value = value * math.exp(LOG_SIGMA * sigma * rng.gauss(0.0, 1.0))
        elif kind == 'wide':
            value = value + WIDE_SIGMA * sigma * rng.gauss(0.0, 1.0)
        else:
            value = value + ADD_SIGMA * sigma * rng.gauss(0.0, 1.0)
        out[key] = clamp(value, low, high)
    return out


def complete(genome, species):
    """A genome from an older file, with any gene it lacks set to the norm."""
    base = dict(JELLY_FOUNDER) if species == 'jelly' else dict(ECOLI_FOUNDER, **ECOLI_REFLEX)
    out = dict(base)
    out.update({k: v for k, v in (genome or {}).items() if k in base})
    return out


def founder(species, rng, spread=2):
    """A fresh individual with no parent: the norm, spread over ``spread`` rounds.

    E. coli founders carry the reflex genes too, so a first population already
    holds seekers and fleers of every sense, for selection to sort.
    """
    base = dict(JELLY_FOUNDER) if species == 'jelly' else dict(ECOLI_FOUNDER, **ECOLI_REFLEX)
    for _ in range(spread):
        base = mutate(base, rng)
    return base


def rounded(genome, places=3):
    return {k: round(v, places) for k, v in genome.items()}


def make_rng(seed, *parts):
    return random.Random(':'.join(str(p) for p in (seed,) + parts))
