"""Stigmergy field: a 2-channel scalar field written and read by creatures.

The creatures have no vision. The only way one individual can influence another
is by changing this field and having the other read it later. Channel 0 is the
food trace (short lived, marks where prey was), channel 1 is the path trace
(long lived, marks where individuals have been).

Coordinates are Unity world XZ (docs/creature-protocol.md section 4). The grid
covers the rectangle ``bounds = [min_x, min_z, max_x, max_z]`` with cell (col,
row) where col indexes X and row indexes Z, row-major, matching the ``field``
message in section 6.5.
"""

import base64
import math

FOOD = 0
PATH = 1

# Half lives in seconds. The food trace fades fast so a stale mark does not
# hold a colony in a place the prey has left; the path trace persists so a
# route stays legible across a whole mission.
DEFAULT_HALF_LIFE = (11.0, 140.0)

# Fraction of the difference to a neighbour exchanged per second. With four
# neighbours the explicit update is stable while rate * dt * 4 < 1.
DEFAULT_DIFFUSION = (0.35, 0.05)


class StigmergyField:
    def __init__(self, bounds, cols=64, rows=64, half_life=DEFAULT_HALF_LIFE,
                 diffusion=DEFAULT_DIFFUSION, channels=2):
        if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
            raise ValueError('bounds must be four finite numbers')
        min_x, min_z, max_x, max_z = bounds
        if max_x <= min_x or max_z <= min_z:
            raise ValueError('bounds must have positive extent')
        if not (isinstance(cols, int) and isinstance(rows, int)) or cols < 2 or rows < 2:
            raise ValueError('cols and rows must be integers >= 2')
        if channels < 1:
            raise ValueError('channels must be >= 1')
        if len(half_life) != channels or len(diffusion) != channels:
            raise ValueError('half_life and diffusion must have one entry per channel')
        for h in half_life:
            if h is not None and (not math.isfinite(h) or h <= 0):
                raise ValueError('half_life must be positive or None')
        for d in diffusion:
            if not math.isfinite(d) or d < 0:
                raise ValueError('diffusion must be nonnegative')

        self.bounds = list(bounds)
        self.cols = cols
        self.rows = rows
        self.channels = channels
        self.half_life = list(half_life)
        self.diffusion = list(diffusion)
        self.cell_size_x = (max_x - min_x) / cols
        self.cell_size_z = (max_z - min_z) / rows
        self.cells = [[0.0] * (cols * rows) for _ in range(channels)]

    @property
    def cell_size(self):
        """Single cell size for the protocol ``field`` message.

        The message carries one scalar, so a non-square grid reports its X size.
        Keep cols/rows proportional to the bounds to avoid the mismatch.
        """
        return self.cell_size_x

    @property
    def origin(self):
        return [self.bounds[0], self.bounds[1]]

    def total(self, channel):
        return sum(self.cells[channel])

    # -- world <-> grid -------------------------------------------------

    def _grid(self, x, z):
        """Continuous grid coordinates with cell centres on integers."""
        gx = (x - self.bounds[0]) / self.cell_size_x - 0.5
        gz = (z - self.bounds[1]) / self.cell_size_z - 0.5
        return gx, gz

    def _corners(self, x, z):
        """The four cells around (x, z) with their bilinear weights."""
        gx, gz = self._grid(x, z)
        x0 = math.floor(gx)
        z0 = math.floor(gz)
        fx = gx - x0
        fz = gz - z0
        out = []
        for dz, wz in ((0, 1.0 - fz), (1, fz)):
            for dx, wx in ((0, 1.0 - fx), (1, fx)):
                w = wx * wz
                if w == 0.0:
                    continue
                col = min(self.cols - 1, max(0, x0 + dx))
                row = min(self.rows - 1, max(0, z0 + dz))
                out.append((row * self.cols + col, w))
        return out

    def cell_index(self, x, z):
        """Index of the cell containing (x, z), clamped to the grid."""
        col = int((x - self.bounds[0]) / self.cell_size_x)
        row = int((z - self.bounds[1]) / self.cell_size_z)
        col = min(self.cols - 1, max(0, col))
        row = min(self.rows - 1, max(0, row))
        return row * self.cols + col

    # -- read and write -------------------------------------------------

    def deposit(self, x, z, channel, amount):
        """Add ``amount`` at (x, z), split bilinearly over the nearest cells.

        The full amount always lands in the grid, so a deposit followed by a
        diffusion step conserves the channel total exactly.
        """
        if amount == 0.0:
            return
        if not math.isfinite(amount):
            raise ValueError('amount must be finite')
        grid = self.cells[channel]
        for index, weight in self._corners(x, z):
            grid[index] += amount * weight

    def sample(self, x, z, channel):
        """Bilinear read at (x, z). Outside the bounds the edge value is held."""
        grid = self.cells[channel]
        return sum(grid[index] * weight for index, weight in self._corners(x, z))

    # -- time -----------------------------------------------------------

    def decay(self, dt):
        for channel in range(self.channels):
            half = self.half_life[channel]
            if half is None:
                continue
            factor = 0.5 ** (dt / half)
            grid = self.cells[channel]
            for i in range(len(grid)):
                grid[i] *= factor

    def diffuse(self, dt):
        """Exchange with the four neighbours. Conserves each channel's total.

        Every interior edge moves the same amount out of one cell and into the
        other, so the sum over the grid is unchanged, including at the border
        where cells simply have fewer neighbours.
        """
        cols, rows = self.cols, self.rows
        for channel in range(self.channels):
            rate = self.diffusion[channel] * dt
            if rate <= 0.0:
                continue
            # Keep the explicit update stable even with a long dt.
            rate = min(rate, 0.25)
            old = self.cells[channel]
            new = list(old)
            for row in range(rows):
                base = row * cols
                for col in range(cols):
                    i = base + col
                    here = old[i]
                    flux = 0.0
                    if col > 0:
                        flux += old[i - 1] - here
                    if col < cols - 1:
                        flux += old[i + 1] - here
                    if row > 0:
                        flux += old[i - cols] - here
                    if row < rows - 1:
                        flux += old[i + cols] - here
                    new[i] = here + rate * flux
            self.cells[channel] = new

    def update(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be nonnegative and finite')
        if dt == 0.0:
            return
        self.decay(dt)
        self.diffuse(dt)

    def clear(self):
        for grid in self.cells:
            for i in range(len(grid)):
                grid[i] = 0.0

    # -- transport ------------------------------------------------------

    def to_bytes(self, channel, scale=1.0):
        """Row-major 0..255 bytes for the protocol ``field`` message.

        ``scale`` is the field value that maps to 255; larger values clamp.
        """
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError('scale must be positive')
        grid = self.cells[channel]
        out = bytearray(len(grid))
        for i, v in enumerate(grid):
            if v <= 0.0:
                continue
            out[i] = 255 if v >= scale else int(v / scale * 255.0)
        return bytes(out)

    def to_base64(self, channel, scale=1.0):
        return base64.b64encode(self.to_bytes(channel, scale)).decode('ascii')
