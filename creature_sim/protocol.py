"""Creature Link Protocol v1: validation and message building.

The wire format is defined in docs/creature-protocol.md, which is kept
identical in this repository and in mech-arena-quest. Fixed samples of every
message live in docs/creature-protocol-samples/ and are the conformance
fixtures for both sides.

Anything arriving over the socket is untrusted: ``parse`` never raises, it
returns an ``error`` message describing what was wrong.
"""

import base64
import json

VERSION = 1
MAX_DATAGRAM = 60000
CELLS = 16
EAR_COUNT = 4

TYPES = ('hello', 'welcome', 'observe', 'command', 'field', 'bye', 'error')
CREATURE_STATES = ('spawned', 'alive', 'dead')
DEATH_REASONS = ('shot', 'starved', 'despawn')
MODES = ('run', 'tumble', 'idle')

VERSION_MISMATCH = 'version_mismatch'
UNKNOWN_SESSION = 'unknown_session'
MALFORMED = 'malformed'
TOO_MANY_CREATURES = 'too_many_creatures'


class ProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _envelope(message_type, session, tick):
    return dict(v=VERSION, type=message_type, session=session, tick=int(tick))


def make_error(code, message, session=None, tick=0):
    out = _envelope('error', session, tick)
    out['code'] = code
    out['message'] = str(message)[:400]
    return out


# -- reading ------------------------------------------------------------

def _require(data, key, types, where):
    if key not in data:
        raise ProtocolError(MALFORMED, f'{where}: missing {key}')
    value = data[key]
    if not isinstance(value, types) or isinstance(value, bool) and types is not bool:
        raise ProtocolError(MALFORMED, f'{where}: {key} has the wrong type')
    return value


def _number(data, key, where, default=None):
    if key not in data:
        if default is None:
            raise ProtocolError(MALFORMED, f'{where}: missing {key}')
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(MALFORMED, f'{where}: {key} is not a number')
    value = float(value)
    if value != value or value in (float('inf'), float('-inf')):
        raise ProtocolError(MALFORMED, f'{where}: {key} is not finite')
    return value


def _vector(data, key, length, where):
    value = _require(data, key, (list, tuple), where)
    if len(value) != length:
        raise ProtocolError(MALFORMED, f'{where}: {key} needs {length} values')
    out = []
    for i, v in enumerate(value):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ProtocolError(MALFORMED, f'{where}: {key}[{i}] is not a number')
        out.append(float(v))
    return out


def validate(data):
    """Check a decoded message. Returns it unchanged or raises ProtocolError."""
    if not isinstance(data, dict):
        raise ProtocolError(MALFORMED, 'message is not an object')
    if data.get('v') != VERSION:
        raise ProtocolError(VERSION_MISMATCH, f"expected v={VERSION}, got {data.get('v')!r}")
    message_type = data.get('type')
    if message_type not in TYPES:
        raise ProtocolError(MALFORMED, f'unknown type {message_type!r}')
    _require(data, 'session', str, message_type)
    tick = data.get('tick', 0)
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise ProtocolError(MALFORMED, f'{message_type}: tick must be a nonnegative integer')

    if message_type == 'hello':
        _validate_hello(data)
    elif message_type == 'observe':
        _validate_observe(data)
    elif message_type == 'command':
        _validate_command(data)
    return data


def _validate_hello(data):
    body = _require(data, 'body', dict, 'hello')
    cells = body.get('cells', CELLS)
    if cells != CELLS:
        raise ProtocolError(MALFORMED, f'hello: body.cells is {cells}, this brain needs {CELLS}')
    for key in ('length', 'radius', 'runSpeed'):
        if _number(body, key, 'hello.body', 0.0) < 0:
            raise ProtocolError(MALFORMED, f'hello.body: {key} must be nonnegative')
    arena = _require(data, 'arena', dict, 'hello')
    bounds = _vector(arena, 'bounds', 4, 'hello.arena')
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ProtocolError(MALFORMED, 'hello.arena: bounds must have positive extent')
    for i, obstacle in enumerate(arena.get('obstacles') or []):
        where = f'hello.arena.obstacles[{i}]'
        if not isinstance(obstacle, dict):
            raise ProtocolError(MALFORMED, where + ' is not an object')
        shape = obstacle.get('shape')
        if shape not in ('circle', 'box'):
            raise ProtocolError(MALFORMED, f'{where}: unknown shape {shape!r}')
        _vector(obstacle, 'center', 2, where)
        if shape == 'circle':
            if _number(obstacle, 'radius', where) <= 0:
                raise ProtocolError(MALFORMED, where + ': radius must be positive')
        else:
            size = _vector(obstacle, 'size', 2, where)
            if size[0] <= 0 or size[1] <= 0:
                raise ProtocolError(MALFORMED, where + ': size must be positive')


def _validate_observe(data):
    _number(data, 'dt', 'observe', 1.0 / 30.0)
    for key in ('sounds', 'prey'):
        if key in data and not isinstance(data[key], (list, tuple)):
            raise ProtocolError(MALFORMED, f'observe: {key} must be a list')
    creatures = _require(data, 'creatures', (list, tuple), 'observe')
    seen = set()
    for i, creature in enumerate(creatures):
        where = f'observe.creatures[{i}]'
        if not isinstance(creature, dict):
            raise ProtocolError(MALFORMED, where + ' is not an object')
        creature_id = creature.get('id')
        if isinstance(creature_id, bool) or not isinstance(creature_id, int) or creature_id < 0:
            raise ProtocolError(MALFORMED, where + ': id must be a nonnegative integer')
        if creature_id in seen:
            raise ProtocolError(MALFORMED, f'{where}: id {creature_id} appears twice')
        seen.add(creature_id)
        state = creature.get('state', 'alive')
        if state not in CREATURE_STATES:
            raise ProtocolError(MALFORMED, f'{where}: unknown state {state!r}')
        if state == 'dead' and creature.get('reason') not in (None,) + DEATH_REASONS:
            raise ProtocolError(MALFORMED, f"{where}: unknown reason {creature.get('reason')!r}")
        _vector(creature, 'pos', 3, where)
        _number(creature, 'heading', where)
        if 'ears' in creature:
            ears = _vector(creature, 'ears', EAR_COUNT, where)
            if any(not 0.0 <= v <= 1.0 for v in ears):
                raise ProtocolError(MALFORMED, f'{where}: ears must be in 0..1')
        cells =_require(creature, 'cells', (list, tuple), where)
        if len(cells) != CELLS:
            raise ProtocolError(MALFORMED, f'{where}: needs {CELLS} cells, got {len(cells)}')
        for j, cell in enumerate(cells):
            if not isinstance(cell, dict):
                raise ProtocolError(MALFORMED, f'{where}.cells[{j}] is not an object')
            _number(cell, 'p', f'{where}.cells[{j}]', 0.0)
            kind = cell.get('k', 0)
            if isinstance(kind, bool) or not isinstance(kind, int) or not 0 <= kind <= 3:
                raise ProtocolError(MALFORMED, f'{where}.cells[{j}]: k must be 0..3')
            _number(cell, 'h', f'{where}.cells[{j}]', 0.0)


def _validate_command(data):
    creatures = _require(data, 'creatures', (list, tuple), 'command')
    for i, creature in enumerate(creatures):
        where = f'command.creatures[{i}]'
        if not isinstance(creature, dict):
            raise ProtocolError(MALFORMED, where + ' is not an object')
        if creature.get('mode') not in MODES:
            raise ProtocolError(MALFORMED, f"{where}: unknown mode {creature.get('mode')!r}")
        _number(creature, 'speed', where, 0.0)
        _number(creature, 'turn', where, 0.0)


def parse(payload):
    """Decode a datagram. Never raises: a bad message comes back as ``error``."""
    if isinstance(payload, (bytes, bytearray, memoryview)):
        if len(payload) > MAX_DATAGRAM:
            return make_error(MALFORMED, f'datagram is {len(payload)} bytes, over the {MAX_DATAGRAM} limit')
        try:
            text = bytes(payload).decode('utf-8')
        except UnicodeDecodeError as error:
            return make_error(MALFORMED, f'not UTF-8: {error}')
    else:
        text = payload
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as error:
        return make_error(MALFORMED, f'not JSON: {error}')
    session = data.get('session') if isinstance(data, dict) else None
    tick = data.get('tick') if isinstance(data, dict) else 0
    try:
        return validate(data)
    except ProtocolError as error:
        return make_error(error.code, error.message, session,
                          tick if isinstance(tick, int) and tick >= 0 else 0)


# -- writing ------------------------------------------------------------

def make_welcome(session, brain, cols=64, rows=64, channels=2, max_creatures=12, tick=0):
    from .ecoli.brain import INPUTS, OUTPUTS
    out = _envelope('welcome', session, tick)
    out['brain'] = dict(species=brain.species, generation=brain.generation,
                        fingerprint=brain.fingerprint,
                        inputs=INPUTS, outputs=OUTPUTS)
    out['field'] = dict(cols=int(cols), rows=int(rows), channels=int(channels))
    out['maxCreatures'] = int(max_creatures)
    return out


def make_command(session, tick, creatures):
    out = _envelope('command', session, tick)
    out['creatures'] = [
        dict(id=int(c['id']), mode=c['mode'],
             speed=round(float(c.get('speed', 0.0)), 4),
             turn=round(float(c.get('turn', 0.0)), 4),
             deposit=round(float(c.get('deposit', 0.0)), 4),
             energy=round(float(c.get('energy', 0.0)), 4),
             starved=bool(c.get('starved', False)))
        for c in creatures]
    return out


def make_field(session, tick, field, channel, scale=1.0):
    out = _envelope('field', session, tick)
    out['cols'] = field.cols
    out['rows'] = field.rows
    out['channel'] = int(channel)
    out['origin'] = list(field.origin)
    out['cellSize'] = field.cell_size
    out['data'] = field.to_base64(channel, scale)
    return out


def make_bye(session, tick, reason='mission_end'):
    out = _envelope('bye', session, tick)
    out['reason'] = reason
    return out


def encode(message):
    """Serialise a message, refusing anything that will not fit a datagram."""
    payload = json.dumps(message, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_DATAGRAM:
        raise ProtocolError(MALFORMED,
                            f"{message.get('type')} is {len(payload)} bytes, over the "
                            f'{MAX_DATAGRAM} limit')
    return payload


def decode_field_data(message):
    """Bytes carried by a ``field`` message, checked against cols and rows."""
    raw = base64.b64decode(message['data'])
    expected = message['cols'] * message['rows']
    if len(raw) != expected:
        raise ProtocolError(MALFORMED, f'field data is {len(raw)} bytes, expected {expected}')
    return raw
