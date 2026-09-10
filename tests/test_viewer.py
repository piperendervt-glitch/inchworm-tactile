import json
import shutil
import tempfile
import unittest
from pathlib import Path

from creature_sim import protocol
from creature_sim.field import FOOD, PATH, StigmergyField
from creature_sim.server import BrainServer, SessionLog
from creature_sim.viewer import (LogSource, MirrorSource, cell_world_xz,
                                 decode_field)

SAMPLES = Path(__file__).resolve().parents[1] / 'docs' / 'creature-protocol-samples'


def sample(name):
    return json.loads((SAMPLES / (name + '.json')).read_text(encoding='utf-8'))


def write_session(directory, ticks=10, field_every=5):
    """A session log shaped exactly like the server writes one."""
    log = SessionLog(directory)
    log.meta(sample('hello'), sample('welcome'))
    field = StigmergyField([-40.0, -40.0, 40.0, 40.0])
    for tick in range(ticks):
        observe = sample('observe')
        observe['tick'] = tick
        observe['t'] = round(tick / 30, 4)
        command = sample('command')
        command['tick'] = tick
        message = None
        if tick % field_every == 0:
            field.deposit(float(tick) - 20.0, 0.0, FOOD, 4.0)
            message = protocol.make_field('s', tick, field, FOOD, scale=4.0)
        log.write(observe, command, message)
    log.close()
    return log.path


class LogSourceTests(unittest.TestCase):
    def test_frames_come_back_in_tick_order_with_their_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_session(Path(tmp) / 'session', ticks=10, field_every=5)
            source = LogSource(Path(tmp) / 'session')

            self.assertEqual(len(source), 10)
            frames = list(source.frames())
            self.assertEqual([f.tick for f in frames], list(range(10)))
            for frame in frames:
                self.assertEqual(frame.observe['type'], 'observe')
                self.assertEqual(frame.command['type'], 'command')
                self.assertEqual(frame.observe['tick'], frame.tick)
                self.assertEqual(frame.command['tick'], frame.tick)
            # field is carried only on the ticks that had one
            self.assertEqual([f.tick for f in frames if f.field], [0, 5])
            self.assertEqual(frames[0].field['type'], 'field')

            # meta.json rides along, so the arena is known without a hello
            self.assertEqual(source.hello['type'], 'hello')
            self.assertEqual(source.hello['arena']['bounds'], [-40, -40, 40, 40])
            source.close()

    def test_random_access_matches_sequential_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_session(Path(tmp) / 'session', ticks=20)
            source = LogSource(Path(tmp) / 'session')
            sequential = list(source.frames())
            for index in (19, 0, 13, 4, 19, 0):
                self.assertEqual(source.frame_at(index).tick, sequential[index].tick)
            self.assertIsNone(source.frame_at(20))
            self.assertIsNone(source.frame_at(-1))
            source.close()

    def test_a_growing_file_is_picked_up_and_a_half_line_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'session'
            path = write_session(directory, ticks=3)
            source = LogSource(directory)
            self.assertEqual(len(source), 3)

            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(dict(tick=3, t=0.1, observe=sample('observe'),
                                        command=sample('command'))))
                f.write('\n')
                f.flush()
                self.assertEqual(source.refresh(), 1)
                self.assertEqual(len(source), 4)
                self.assertEqual(source.frame_at(3).tick, 3)

                # A line still being written must not be handed out.
                f.write('{"tick":4,"t":0.13,"obser')
                f.flush()
                self.assertEqual(source.refresh(), 0)
                self.assertEqual(len(source), 4)

                f.write('ve":null,"command":null}\n')
                f.flush()
                self.assertEqual(source.refresh(), 1)
                self.assertEqual(source.frame_at(4).tick, 4)
            source.close()


class FieldDecodeTests(unittest.TestCase):
    def test_field_message_decodes_to_a_64_by_64_grid(self):
        message = sample('field')
        channel, cols, rows, raw = decode_field(message)

        self.assertEqual((channel, cols, rows), (0, 64, 64))
        self.assertEqual(len(raw), 64 * 64)
        # Row major, so cell (col, row) is at row * cols + col. The fixture
        # stores (row * 4 + col) % 256.
        for col, row in ((0, 0), (5, 2), (63, 63), (31, 40)):
            self.assertEqual(raw[row * cols + col], (row * 4 + col) % 256)

    def test_a_written_field_survives_the_round_trip_to_pixels(self):
        field = StigmergyField([-40.0, -40.0, 40.0, 40.0])
        col, row = 31, 40
        x = -40.0 + field.cell_size_x * (col + 0.5)
        z = -40.0 + field.cell_size_z * (row + 0.5)
        field.deposit(x, z, FOOD, 4.0)
        field.deposit(x, z, PATH, 2.0)

        food = decode_field(protocol.make_field('s', 0, field, FOOD, scale=4.0))[3]
        path = decode_field(protocol.make_field('s', 0, field, PATH, scale=4.0))[3]

        self.assertEqual(food[row * 64 + col], 255)
        self.assertEqual(path[row * 64 + col], 127)
        self.assertEqual(sum(1 for v in food if v), 1)
        # Channels are independent: one message carries one of them.
        self.assertNotEqual(food[row * 64 + col], path[row * 64 + col])

    def test_the_two_channels_paint_red_and_blue_and_stay_separate(self):
        from creature_sim.viewer import ARENA_FILL, FieldImage

        field = StigmergyField([-40.0, -40.0, 40.0, 40.0])
        food_col, food_row = 10, 20
        path_col, path_row = 50, 30
        for col, row, channel in ((food_col, food_row, FOOD), (path_col, path_row, PATH)):
            field.deposit(-40.0 + field.cell_size_x * (col + 0.5),
                          -40.0 + field.cell_size_z * (row + 0.5), channel, 4.0)

        picture = FieldImage()
        picture.update(protocol.make_field('s', 0, field, FOOD, scale=4.0))
        picture.update(protocol.make_field('s', 0, field, PATH, scale=4.0))
        rows = picture.rows_of_colours().split('} {')
        self.assertEqual(len(rows), 64)

        def colour(col, row):
            # Field row 0 is the far edge and is painted last, at the bottom.
            return rows[63 - row].strip('{}').split()[col]

        food = colour(food_col, food_row)
        path = colour(path_col, path_row)
        # Food is red-dominant, path is blue-dominant, and neither bleeds into
        # the other's cell.
        self.assertGreater(int(food[1:3], 16), int(food[5:7], 16))
        self.assertLess(int(path[1:3], 16), int(path[5:7], 16))
        self.assertEqual(colour(path_col, food_row), ARENA_FILL)
        self.assertEqual(colour(0, 0), ARENA_FILL)

    def test_the_picture_is_the_same_way_up_as_the_arena(self):
        from creature_sim.viewer import ARENA_FILL, FieldImage

        field = StigmergyField([-40.0, -40.0, 40.0, 40.0])
        # Near the top edge of the arena (largest Z) and to the right.
        field.deposit(30.0, 39.0, PATH, 4.0)
        picture = FieldImage()
        picture.update(protocol.make_field('s', 0, field, PATH, scale=4.0))
        rows = picture.rows_of_colours().split('} {')

        lit = [i for i, r in enumerate(rows)
               if any(c != ARENA_FILL for c in r.strip('{}').split())]
        # The first row string is the top of the picture, where the arena's
        # top edge is drawn, so the trace must be in the first rows.
        self.assertTrue(lit and max(lit) <= 1, f'trace landed in rows {lit}')
        # And on the right-hand side.
        cols = [i for i, c in enumerate(rows[lit[0]].strip('{}').split()) if c != ARENA_FILL]
        self.assertGreater(min(cols), 50)

    def test_a_faint_trace_is_still_visible_against_the_floor(self):
        from creature_sim.viewer import ARENA_FILL, FieldImage

        floor_blue = int(ARENA_FILL[5:7], 16)
        picture = FieldImage(cols=2, rows=1)
        picture.channels[PATH] = bytes([8, 0])
        faint = picture.rows_of_colours().strip('{}').split()[0]
        # One eighth of a percent of full strength must not read as floor.
        self.assertGreater(int(faint[5:7], 16), floor_blue + 30)

    def test_a_truncated_field_is_refused(self):
        message = sample('field')
        message['rows'] = 32
        with self.assertRaises(protocol.ProtocolError):
            decode_field(message)


class RecordingSocket:
    """Stands in for a socket. Several share one log, so the order of sends
    across the Body socket and the watcher socket can be read off."""

    def __init__(self, log=None, refuse=()):
        self.sent = log if log is not None else []
        self.refuse = set(refuse)
        self.closed = False

    def sendto(self, payload, address):
        if address in self.refuse:
            raise ConnectionResetError('nobody there')
        self.sent.append((address, protocol.parse(payload)))

    def close(self):
        self.closed = True

    def kinds(self, address):
        return [m.get('type') for a, m in self.sent if a == address]


class MirrorTests(unittest.TestCase):
    BODY = ('10.0.0.5', 5000)
    WATCHER = ('127.0.0.1', 47130)

    def build(self, mirror='127.0.0.1:47130', refuse=()):
        tmp = tempfile.mkdtemp()
        server = BrainServer(seed=0, quiet=True, sessions_dir=Path(tmp) / 's',
                             mirror=mirror)
        self.log = []
        server.socket = RecordingSocket(self.log)
        server.mirror_socket = RecordingSocket(self.log, refuse=refuse)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(server.close_session, 'test')
        return server

    def exchange(self, server, message):
        replies = server.handle(message)
        for reply in replies:
            server.send(reply, self.BODY)
        server.mirror_send([message] + replies)
        return replies

    def test_the_body_is_answered_before_anything_is_mirrored(self):
        server = self.build()
        self.assertEqual(server.mirror, self.WATCHER)

        self.exchange(server, sample('hello'))
        self.exchange(server, self.observe(server, tick=0))

        order = [address for address, _ in self.log]
        first_watcher = order.index(self.WATCHER)
        # Everything the Body gets is already sent before the watcher is told.
        self.assertTrue(all(a == self.BODY for a in order[:first_watcher]))
        self.assertEqual(server.socket.kinds(self.BODY), ['welcome', 'command'])
        self.assertEqual(server.socket.kinds(self.WATCHER),
                         ['hello', 'welcome', 'observe', 'command'])

    def test_the_watcher_gets_its_own_socket(self):
        # A refused copy raises on the socket that sent it, so the one the
        # Body depends on must not be that socket.
        server = self.build()
        self.assertIsNot(server.socket, server.mirror_socket)

    def test_a_field_tick_mirrors_the_field_too(self):
        server = self.build()
        server.handle(sample('hello'))
        # t past the half-second field period, so a field is produced.
        self.exchange(server, self.observe(server, tick=30, t=1.0))

        self.assertEqual(server.socket.kinds(self.BODY), ['command', 'field'])
        self.assertEqual(server.socket.kinds(self.WATCHER),
                         ['observe', 'command', 'field'])

    def test_without_the_flag_nothing_is_mirrored(self):
        server = self.build(mirror=None)
        self.assertIsNone(server.mirror)
        server.handle(sample('hello'))
        server.mirror_send([sample('observe')])
        self.assertEqual(self.log, [])
        self.assertEqual(server.mirrored, 0)

    def test_a_watcher_that_is_gone_does_not_stop_the_server(self):
        server = self.build(refuse=[self.WATCHER])
        self.exchange(server, sample('hello'))
        self.exchange(server, self.observe(server, tick=0))
        self.exchange(server, self.observe(server, tick=1))

        self.assertEqual(server.mirrored, 0)
        self.assertEqual(server.mirrored_failures, 3)
        # The Body was served throughout.
        self.assertEqual(server.socket.kinds(self.BODY),
                         ['welcome', 'command', 'command'])

    def observe(self, server, tick=0, t=0.0):
        message = sample('observe')
        message['session'] = server.session or sample('hello')['session']
        message['tick'] = tick
        message['t'] = t
        for creature in message['creatures']:
            creature['state'] = 'alive'
            creature.pop('reason', None)
        return message


class ResetToleranceTests(unittest.TestCase):
    """On Windows a refused datagram makes the next recvfrom raise. That must
    not end the run: a watcher being closed once took the whole server down."""

    def test_the_serve_loop_rides_out_a_connection_reset(self):
        import socket as socket_module

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        server = BrainServer(seed=0, quiet=True, sessions_dir=Path(tmp) / 's')
        received = []

        class Flaky:
            """Raises the reset once, then serves a real hello, then idles."""

            def __init__(self):
                self.calls = 0

            def setsockopt(self, *a):
                pass

            def ioctl(self, *a):
                pass

            def bind(self, *a):
                pass

            def settimeout(self, *a):
                pass

            def getsockname(self):
                return ('0.0.0.0', 47123)

            def recvfrom(self, _size):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionResetError('ICMP unreachable')
                if self.calls == 2:
                    return protocol.encode(sample('hello')), ('10.0.0.5', 5000)
                raise socket_module.timeout()

            def sendto(self, payload, address):
                received.append(protocol.parse(payload).get('type'))

            def close(self):
                pass

        flaky = Flaky()
        server.socket = flaky
        original = socket_module.socket
        socket_module.socket = lambda *a, **k: flaky
        try:
            server.serve(seconds=0.4)
        finally:
            socket_module.socket = original

        # The reset was shrugged off and the hello that followed was answered.
        self.assertGreater(flaky.calls, 2)
        self.assertEqual(received, ['welcome'])


class MirrorSourceTests(unittest.TestCase):
    """Grouping by tick, without touching a real socket."""

    def source(self):
        source = MirrorSource(0)
        self.addCleanup(source.close)
        return source

    def test_messages_are_grouped_into_one_frame_per_tick(self):
        source = self.source()
        source.feed(sample('hello'))

        released = []
        for tick in range(3):
            for kind in ('observe', 'command'):
                message = sample(kind)
                message['tick'] = tick
                message['t'] = tick / 30
                frame = source.feed(message)
                if frame is not None:
                    released.append(frame)

        # Two ticks are complete and released; the third is still open.
        self.assertEqual([f.tick for f in released], [0, 1])
        for frame in released:
            self.assertIsNotNone(frame.observe)
            self.assertIsNotNone(frame.command)
        self.assertEqual(source.pending.tick, 2)
        self.assertEqual(source.flush().tick, 2)

    def test_a_late_datagram_for_a_released_tick_is_dropped(self):
        source = self.source()
        source.feed(sample('hello'))
        for tick in (0, 1):
            for kind in ('observe', 'command'):
                message = sample(kind)
                message['tick'] = tick
                source.feed(message)
        self.assertEqual(source.released, 0)

        stale = sample('field')
        stale['tick'] = 0
        self.assertIsNone(source.feed(stale))
        self.assertEqual(source.pending.tick, 1)
        self.assertIsNone(source.pending.field)

    def test_hello_is_kept_so_the_arena_is_known(self):
        source = self.source()
        self.assertIsNone(source.hello)
        source.feed(sample('hello'))
        self.assertEqual(source.hello['arena']['bounds'], [-40, -40, 40, 40])
        self.assertEqual(source.session, sample('hello')['session'])


class GeometryTests(unittest.TestCase):
    def test_the_viewer_places_cells_where_the_colony_does(self):
        from creature_sim.ecoli.colony import Colony

        colony = Colony(bounds=[-40, -40, 40, 40], seed=0,
                        settings=dict(length=3.0, radius=0.8))
        for heading in (0.0, 1.0, -2.5):
            for index in range(16):
                self.assertEqual(
                    cell_world_xz(2.0, -3.0, heading, index, 3.0, 0.8),
                    colony.cell_world_xz(2.0, -3.0, heading, index),
                    f'cell {index} at heading {heading}')


if __name__ == '__main__':
    unittest.main()
