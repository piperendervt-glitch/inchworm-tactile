"""Energy, starvation and division, the same for both species.

Energy runs 0..max. Every second costs basal * metab plus motion while moving.
Food arrives as bites (E. coli) or grazing (jelly). Energy at zero is death by
starvation, which the Body carries out. Energy held above the individual's
satiety for a moment is division: the Brain asks, the Body puts a child beside
the parent if there is room, and when the child shows up in the next observe
the parent's energy is halved and the child starts with the other half.

The numbers are tuned for quick turnover, as in the jellyfish prototype: a
full individual that finds nothing dies within about a minute.
"""

# A full E. coli running flat out lasts 100 s; born at 60 it has a minute
# to find its first meal. (1.0 + 1.0 with birth at 100 starved everything
# in forty seconds: founders divided at once and the halves never ate.)
DEFAULTS = dict(
    max_energy=100.0,
    basal_cost=0.5,        # per second, times the metab gene
    motion_cost=0.5,       # per second at full speed
    fed_seconds=2.0,       # how long above satiety before dividing
    divide_cooldown=4.0,   # after asking, wait this long before asking again
    child_share=0.5,       # fraction of the parent's energy the child takes
)


class Physiology:
    """The per-individual part. Attach one to each creature."""

    __slots__ = ('energy', 'fed', 'cooldown', 'starved', 'asked', 'divisions', 'eaten')

    def __init__(self, energy):
        self.energy = float(energy)
        self.fed = 0.0
        self.cooldown = 0.0
        self.starved = False
        self.asked = False
        self.divisions = 0
        self.eaten = 0.0

    def feed(self, amount, max_energy):
        gain = min(max(0.0, amount), max_energy - self.energy)
        self.energy += gain
        self.eaten += gain
        return gain

    def step(self, dt, settings, metab, motion, satiety):
        """Burn energy; return True when the individual should ask to divide."""
        if self.starved:
            return False
        self.energy -= dt * (settings['basal_cost'] * metab + settings['motion_cost'] * motion)
        if self.energy <= 0.0:
            self.energy = 0.0
            self.starved = True
            return False
        self.cooldown = max(0.0, self.cooldown - dt)
        if self.energy >= satiety * settings['max_energy']:
            self.fed += dt
        else:
            self.fed = 0.0
        if self.fed >= settings['fed_seconds'] and self.cooldown <= 0.0:
            self.cooldown = settings['divide_cooldown']
            self.fed = 0.0
            self.asked = True
            return True
        return False

    def split(self, settings):
        """The child appeared: give it its share. Returns the child's energy."""
        share = self.energy * settings['child_share']
        self.energy -= share
        self.divisions += 1
        self.asked = False
        return share
