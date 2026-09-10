"""Brain side of the Creature Link Protocol, over UDP.

Run it next to Unity (or anywhere on the LAN) and it will drive the creatures:

    python -m creature_sim.server --port 47123 --seed 0

The Body owns the physical truth and sends ``observe``; this process owns the
decisions and answers with ``command``. It holds one session at a time. A
``hello`` with a new session id throws away everything from the old one, so a
Unity restart needs no cleanup here.
"""

import argparse
import datetime
import json
import socket
import sys
import time
from pathlib import Path

from .ecoli.brain import EcoliBrain
from .ecoli.colony import Colony
from .field import CHANNELS, FOOD, StigmergyField
from .jelly.colony import JellyColony
from . import lineage, protocol

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 47123
# One channel goes out at a time, so with four of them each is refreshed
# once a second without any single tick carrying more than one picture.
FIELD_PERIOD = 0.25
# Value that reads as full brightness in the debug overlay.
FIELD_SCALE = 4.0


def shield(sock):
    """Stop a refused datagram from poisoning the socket that sent it.

    On Windows an unreachable destination comes back as ICMP, and the next
    recvfrom on that socket raises ConnectionResetError even though nothing is
    wrong with the socket. Left alone it would let a watcher that has been
    closed take the server down with it.
    """
    if hasattr(socket, 'SIO_UDP_CONNRESET'):
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except OSError:
            pass
    return sock


def parse_endpoint(text):
    """\"host:port\" or bare \"port\" into an address tuple."""
    if not text:
        return None
    if ':' in text:
        host, _, port = text.rpartition(':')
        return (host or '127.0.0.1', int(port))
    return ('127.0.0.1', int(text))


class SessionLog:
    """Minimal per-tick record. The real log format comes in a later stage.

    Nothing reaches the disk until the first observe. A Body that says hello
    and then goes away, which a run of the Unity tests does thousands of times,
    would otherwise leave an empty session behind for the learning phase to
    trip over.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'link.jsonl'
        self.file = None
        self.ticks = 0
        self.pending = None

    @property
    def started(self):
        return self.file is not None

    def meta(self, hello, welcome):
        self.pending = dict(schema=1, hello=hello, welcome=welcome,
                            started=datetime.datetime.now().isoformat(timespec='seconds'))

    def _open(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.pending is not None:
            (self.directory / 'meta.json').write_text(
                json.dumps(self.pending, indent=1) + '\n', encoding='utf-8')
        self.file = self.path.open('w', encoding='utf-8')

    def write(self, observation, command, field=None):
        if self.file is None:
            self._open()
        row = dict(tick=observation.get('tick'), t=observation.get('t'),
                   observe=observation, command=command)
        if field is not None:
            row['field'] = field
        self.file.write(json.dumps(row, separators=(',', ':')))
        self.file.write('\n')
        # Flushed every tick so a viewer tailing the file stays in step with
        # the run instead of lagging a buffer behind.
        self.file.flush()
        self.ticks += 1

    def close(self):
        if self.file and not self.file.closed:
            self.file.close()


class BrainServer:
    def __init__(self, port=DEFAULT_PORT, host='0.0.0.0', seed=0, brain_path=None,
                 cols=64, rows=64, max_creatures=32, sessions_dir=None, quiet=False,
                 mirror=None, lineage_path=None, log_every=1):
        self.mirror = parse_endpoint(mirror) if isinstance(mirror, str) else mirror
        # Log one tick in this many. A session that runs for hours would
        # otherwise fill the disk at about 10 MB a game minute. Zero keeps no
        # ticks at all: only the session's meta, summary and lineage.
        self.log_every = max(0, int(log_every))
        # Where survivors' genes are kept between sessions. None keeps nothing.
        self.lineage_path = Path(lineage_path) if lineage_path else None
        self.port = port
        self.host = host
        self.seed = seed
        self.cols = cols
        self.rows = rows
        self.max_creatures = max_creatures
        self.quiet = quiet
        self.sessions_dir = Path(sessions_dir) if sessions_dir else ROOT / 'sessions' / 'creature'
        self.brain = (EcoliBrain.from_json(brain_path) if brain_path
                      else EcoliBrain(seed=seed))
        self.brain_path = brain_path
        self.socket = None
        self.mirror_socket = None
        self.reset()

    # -- session state --------------------------------------------------

    def reset(self):
        self.session = None
        self.colony = None
        self.jellies = None
        self.field = None
        self.log = None
        self.peer = None
        self.last_field = 0.0
        self.field_channel = FOOD
        self.observed = 0
        self.dropped = 0
        self.mirrored = 0
        self.mirrored_failures = 0

    def say(self, *parts):
        if not self.quiet:
            print(*parts, flush=True)

    def close_session(self, reason):
        # With no ticks kept, the folder is still made so the summary and the
        # lineage have somewhere to go, but only once something was observed.
        if self.log and not self.log.started and self.log_every == 0 and self.observed > 0:
            self.log._open()
        if self.log and self.log.started:
            summary = self.colony.summary() if self.colony else {}
            if self.jellies:
                summary['jellies'] = self.jellies.summary()
            if self.lineage_path is not None and reason == 'extinct':
                # Everything died: the line ends here, and the next session starts from the norm.
                lineage.clear(self.lineage_path, session=self.log.directory)
                summary['lineage'] = dict(path=str(self.lineage_path), ecoli=0, jelly=0, extinct=True)
                self.say(f'[lineage] extinct: cleared {self.lineage_path}')
            elif self.lineage_path is not None:
                kept = lineage.save(self.lineage_path, self.colony, self.jellies, session=self.log.directory)
                summary['lineage'] = dict(path=str(self.lineage_path),
                                          ecoli=len(kept['ecoli']), jelly=len(kept['jelly']))
                self.say(f"[lineage] kept {len(kept['ecoli'])} ecoli, {len(kept['jelly'])} jelly -> {self.lineage_path}")
            (self.log.directory / 'summary.json').write_text(
                json.dumps(dict(reason=reason, ticks=self.log.ticks,
                                observed=self.observed, dropped=self.dropped,
                                mirrored=self.mirrored,
                                mirror_failures=self.mirrored_failures,
                                colony=summary), indent=1) + '\n', encoding='utf-8')
            self.say(f'[session] closed ({reason}) after {self.log.ticks} ticks -> {self.log.directory}')
            self.log.close()
        self.reset()

    # -- message handling -----------------------------------------------

    def on_hello(self, message):
        session = message['session']
        if self.session is not None and session != self.session:
            self.close_session('replaced')
        elif self.session == session:
            self.close_session('rehello')

        body = message.get('body') or {}
        arena = message['arena']
        bounds = [float(v) for v in arena['bounds']]
        settings = {}
        for key, source in (('length', 'length'), ('radius', 'radius'),
                            ('run_speed', 'runSpeed')):
            if source in body:
                settings[key] = float(body[source])
        self.field = StigmergyField(bounds, cols=self.cols, rows=self.rows)
        saved = lineage.load(self.lineage_path) if self.lineage_path else dict(ecoli=[], jelly=[])
        self.colony = Colony(brain=self.brain, field=self.field, seed=self.seed,
                             settings=settings, founders=saved['ecoli'])
        jelly_settings = {}
        jelly_body = message.get('jelly') or {}
        if 'runSpeed' in jelly_body:
            jelly_settings['run_speed'] = float(jelly_body['runSpeed'])
        self.jellies = JellyColony(field=self.field, seed=self.seed, settings=jelly_settings,
                                   founders=saved['jelly'])
        if saved['ecoli'] or saved['jelly']:
            self.say(f"[lineage] founders from {self.lineage_path}: "
                     f"{len(saved['ecoli'])} ecoli, {len(saved['jelly'])} jelly")
        self.session = session
        self.last_field = 0.0

        welcome = protocol.make_welcome(session, self.brain, cols=self.field.cols,
                                        rows=self.field.rows,
                                        channels=self.field.channels,
                                        max_creatures=self.max_creatures)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.log = SessionLog(self.sessions_dir / stamp)
        self.log.meta(message, welcome)
        self.say(f'[hello] session={session[:8]} bounds={bounds} '
                 f"generation={self.brain.generation} "
                 f'fingerprint={self.brain.fingerprint[:12]}')
        return [welcome]

    def on_observe(self, message):
        if message['session'] != self.session:
            return [protocol.make_error(protocol.UNKNOWN_SESSION,
                                        'send hello before observe',
                                        message['session'], message.get('tick', 0))]
        creatures = message.get('creatures') or []
        if len(creatures) > self.max_creatures:
            return [protocol.make_error(protocol.TOO_MANY_CREATURES,
                                        f'{len(creatures)} creatures, limit is {self.max_creatures}',
                                        self.session, message.get('tick', 0))]
        # Two colonies share one field: the E. coli colony steps the field's
        # clock and only looks at E. coli; the jellies step after it.
        ecoli_only = dict(message)
        ecoli_only['creatures'] = [c for c in creatures if c.get('species', 'ecoli') == 'ecoli']
        command = self.colony.step(ecoli_only)
        orders = command['creatures'] + self.jellies.step(message)
        command = protocol.make_command(self.session, command['tick'], orders)
        self.observed += 1

        field = None
        now = float(message.get('t', 0.0))
        if now - self.last_field >= FIELD_PERIOD:
            self.last_field = now
            field = protocol.make_field(self.session, command['tick'], self.field,
                                        self.field_channel, FIELD_SCALE)
            # Round robin, so every channel reaches the watcher in turn.
            self.field_channel = (self.field_channel + 1) % self.field.channels

        if self.log and self.log_every > 0 and (self.log_every == 1 or self.observed % self.log_every == 1):
            self.log.write(message, command, field)
        return [command] if field is None else [command, field]

    def handle(self, message):
        kind = message.get('type')
        if kind == 'error':
            self.say(f"[error in] {message.get('code')}: {message.get('message')}")
            return []
        if kind == 'hello':
            return self.on_hello(message)
        if kind == 'observe':
            return self.on_observe(message)
        if kind == 'bye':
            if message['session'] == self.session:
                self.close_session(message.get('reason', 'bye'))
            return []
        if kind in ('welcome', 'command', 'field'):
            # These are ours to send, not to receive.
            return [protocol.make_error(protocol.MALFORMED,
                                        f'{kind} is a Brain-to-Body message',
                                        message.get('session'), message.get('tick', 0))]
        return []

    # -- socket ---------------------------------------------------------

    def send(self, message, address):
        try:
            payload = protocol.encode(message)
        except protocol.ProtocolError as error:
            self.say(f'[drop] {error.message}')
            self.dropped += 1
            return
        self.socket.sendto(payload, address)

    def mirror_send(self, messages):
        """Copy traffic to a watcher. Send-and-forget: nothing is expected back.

        Called only after the Body has been answered, so a watcher can never
        delay the game loop. A watcher that is not running costs one refused
        datagram, which UDP discards silently.
        """
        if not self.mirror:
            return
        # A socket of its own, so a refused copy can never disturb the one
        # that talks to the Body.
        if self.mirror_socket is None:
            self.mirror_socket = shield(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        for message in messages:
            try:
                payload = protocol.encode(message)
            except protocol.ProtocolError:
                continue
            try:
                self.mirror_socket.sendto(payload, self.mirror)
            except OSError:
                # A watcher that has gone away must not take the server with it.
                self.mirrored_failures += 1
                return
            self.mirrored += 1

    def serve(self, seconds=None, ready=None):
        self.socket = shield(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        # No SO_REUSEADDR. On Windows it lets a second server bind the same UDP
        # port without complaint, and the two then split the incoming datagrams
        # between them, which looks exactly like a Body that has half its
        # messages ignored. Better that a duplicate refuses to start.
        try:
            self.socket.bind((self.host, self.port))
        except OSError as error:
            self.socket.close()
            self.socket = None
            raise SystemExit(f'[error] cannot listen on {self.host}:{self.port} '
                             f'({error}); is another server already running?')
        self.socket.settimeout(0.25)
        self.port = self.socket.getsockname()[1]
        self.say(f'[listen] udp {self.host}:{self.port} '
                 f"brain={'file ' + str(self.brain_path) if self.brain_path else 'seed ' + str(self.seed)}"
                 + (f' mirror={self.mirror[0]}:{self.mirror[1]}' if self.mirror else ''))
        if ready is not None:
            ready.set()

        deadline = None if seconds is None else time.monotonic() + seconds
        try:
            while deadline is None or time.monotonic() < deadline:
                try:
                    payload, address = self.socket.recvfrom(65535)
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    # A previous send was refused. Nothing is wrong here.
                    continue
                except OSError:
                    break
                message = protocol.parse(payload)
                if message.get('type') == 'error':
                    self.say(f"[reject] {message['code']}: {message['message']}")
                    self.send(message, address)
                    continue
                self.peer = address
                replies = self.handle(message)
                for reply in replies:
                    self.send(reply, address)
                # Strictly after the Body has its answer.
                if message.get('type') in ('hello', 'observe', 'bye'):
                    self.mirror_send([message] + replies)
        except KeyboardInterrupt:
            self.say('[stop] interrupted')
        finally:
            self.close_session('server_stop')
            self.socket.close()
            self.socket = None
            if self.mirror_socket is not None:
                self.mirror_socket.close()
                self.mirror_socket = None


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.server',
                                description='Brain side of the Creature Link Protocol')
    p.add_argument('--port', type=int, default=DEFAULT_PORT)
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--brain', help='weight JSON written by the learning phase')
    p.add_argument('--cols', type=int, default=64)
    p.add_argument('--rows', type=int, default=64)
    p.add_argument('--max-creatures', type=int, default=32)
    p.add_argument('--sessions', help='where to write session logs')
    p.add_argument('--mirror', metavar='HOST:PORT',
                   help='send a copy of observe, command and field to a watcher')
    p.add_argument('--seconds', type=float, help='stop after this long (for tests)')
    p.add_argument('--lineage', default=str(ROOT / 'brains' / 'lineage.json'),
                   help="survivors' genes, carried from one session to the next")
    p.add_argument('--no-lineage', action='store_true', help='start every session from the norm')
    p.add_argument('--log-every', type=int, default=1,
                   help='log one tick in N (for sessions that run for hours); 0 keeps no ticks, only summary and lineage')
    p.add_argument('--quiet', action='store_true')
    a = p.parse_args(argv)
    server = BrainServer(port=a.port, host=a.host, seed=a.seed, brain_path=a.brain,
                         cols=a.cols, rows=a.rows, max_creatures=a.max_creatures,
                         sessions_dir=a.sessions, quiet=a.quiet, mirror=a.mirror,
                         lineage_path=None if a.no_lineage else a.lineage, log_every=a.log_every)
    server.serve(seconds=a.seconds)
    return 0


if __name__ == '__main__':
    sys.exit(main())
