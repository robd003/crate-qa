import os
import sys
import math
import time
import shutil
import string
import tempfile
import functools
from unittest.case import _Outcome
from pprint import pformat
from threading import Thread
from collections import OrderedDict
from typing import Dict, Any, NamedTuple
from distutils.version import StrictVersion as V
from faker.generator import random
from cr8.run_crate import CrateNode, LineBuffer, get_crate, _extract_version
from cr8.insert_fake_data import SELLECT_COLS, create_row_generator
from cr8.insert_json import to_insert

DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
CRATEDB_0_57 = V('0.57.0')


print_error = functools.partial(print, file=sys.stderr)


def gen_id() -> str:
    return ''.join([random.choice(string.hexdigits) for x in range(12)])


def test_settings(version: V) -> Dict[str, Any]:
    s = {
        'cluster.routing.allocation.disk.watermark.low': '1024k',
        'cluster.routing.allocation.disk.watermark.high': '512k',
    }
    if version >= V('3.0'):
        s.update({
            'cluster.routing.allocation.disk.watermark.flood_stage': '256k',
        })
    return s


def version_tuple_to_strict_version(version_tuple: tuple) -> V:
    return V('.'.join([str(v) for v in version_tuple]))


def columns_for_table(conn, schema, table):
    c = conn.cursor()
    c.execute("SELECT min(version['number']) FROM sys.nodes")
    version = V(c.fetchone()[0])
    stmt = SELLECT_COLS.format(
        schema_column_name='table_schema' if version >= CRATEDB_0_57 else 'schema_name')
    c.execute(stmt, (schema, table, ))
    return OrderedDict(c.fetchall())


def insert_data(conn, schema, table, num_rows):
    cols = columns_for_table(conn, schema, table)
    stmt, args = to_insert(f'"{schema}"."{table}"', cols)
    gen_row = create_row_generator(cols)
    c = conn.cursor()
    c.executemany(stmt, [gen_row() for x in range(num_rows)])
    c.execute(f'REFRESH TABLE "{schema}"."{table}"')


def wait_for_active_shards(cursor, num_active=0, timeout=60, f=1.2):
    """Wait for shards to become active

    If `num_active` is `0` this will wait until there are no shards that aren't
    started.
    If `num_active > 0` this will wait until there are `num_active` shards with
    the state `STARTED`
    """
    waited = 0
    duration = 0.1
    while waited < timeout:
        if num_active > 0:
            cursor.execute(
                "SELECT count(*) FROM sys.shards where state = 'STARTED'")
            if int(cursor.fetchone()[0]) == num_active:
                return
        else:
            cursor.execute(
                "SELECT count(*) FROM sys.shards WHERE state != 'STARTED'")
            if int(cursor.fetchone()[0]) == 0:
                return
        time.sleep(duration)
        waited += duration
        duration *= f

    if DEBUG:
        print('-' * 70)
        print(f'waited: {waited} last duration: {duration} timeout: {timeout}')
        cursor.execute('SELECT count(*), table_name, state FROM sys.shards GROUP BY 2, 3 ORDER BY 2')
        rs = cursor.fetchall()
        print(f'=== {rs}')
        print('-' * 70)
    raise TimeoutError(f"Shards didn't become active within {timeout}s.")


class VersionDef(NamedTuple):
    version: str
    upgrade_segments: bool


class CrateCluster:

    def __init__(self, nodes=[]):
        self._nodes = nodes

    def start(self):
        threads = []
        for node in self._nodes:
            t = Thread(target=node.start)
            t.start()
            threads.append(t)
        [t.join() for t in threads]

    def stop(self):
        for node in self._nodes:
            node.stop()

    def node(self):
        return random.choice(self._nodes)

    def __next__(self):
        return next(self._nodes)

    def __setitem__(self, idx, node):
        self._nodes[idx] = node

    def __getitem__(self, idx):
        return self._nodes[idx]


class NodeProvider:

    CRATE_VERSION = os.environ.get('CRATE_VERSION', 'latest-nightly')
    CRATE_HEAP_SIZE = os.environ.get('CRATE_HEAP_SIZE', '512m')
    DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'

    # _outcome is an attribute of unittest.TestCase
    # we need to declare it so that static analysis with mypy does not fail
    _outcome: _Outcome

    def __init__(self, *args, **kwargs):
        self.tmpdirs = []
        super().__init__(*args, **kwargs)

    def mkdtemp(self, *args):
        tmp = tempfile.mkdtemp()
        self.tmpdirs.append(tmp)
        return os.path.join(tmp, *args)

    def _unicast_hosts(self, num, transport_port=4300):
        return ','.join([
            '127.0.0.1:' + str(transport_port + x)
            for x in range(num)
        ])

    def _new_cluster(self, version, num_nodes, settings={}):
        self.assertTrue(hasattr(self, '_new_node'))
        for port in ['transport.tcp.port', 'http.port', 'psql.port']:
            self.assertFalse(port in settings)
        s = {
            'cluster.name': gen_id(),
            'discovery.zen.ping.unicast.hosts': self._unicast_hosts(num_nodes),
            'discovery.zen.minimum_master_nodes': math.floor(num_nodes / 2.0 + 1),
            'gateway.recover_after_nodes': num_nodes,
            'gateway.expected_nodes': num_nodes,
            'node.max_local_storage_nodes': num_nodes,
        }
        s.update(settings)
        nodes = []
        for id in range(num_nodes):
            s['node.name'] = s['cluster.name'] + '-' + str(id)
            nodes.append(self._new_node(version, s)[0])
        return CrateCluster(nodes)

    def upgrade_node(self, old_node, new_version):
        old_node.stop()
        self._on_stop.remove(old_node)
        (new_node, _) = self._new_node(new_version, old_node._settings)
        new_node.start()
        return new_node

    def setUp(self):
        self._path_data = self.mkdtemp()
        self._on_stop = []
        self._log_consumers = []

        def new_node(version, settings={}):
            crate_dir = get_crate(version)
            version_tuple = _extract_version(crate_dir)
            v = version_tuple_to_strict_version(version_tuple)
            s = {
                'path.data': self._path_data,
                'cluster.name': 'crate-qa',
            }
            s.update(settings)
            s.update(test_settings(v))
            e = {
                'CRATE_HEAP_SIZE': self.CRATE_HEAP_SIZE,
                'CRATE_HOME': crate_dir,
            }

            if self.DEBUG:
                print(f'# Running CrateDB {version} ({v}) ...')
                s_nice = pformat(s)
                print(f'with settings: {s_nice}')
                e_nice = pformat(e)
                print(f'with environment: {e_nice}')

            n = CrateNode(
                crate_dir=crate_dir,
                keep_data=True,
                settings=s,
                env=e,
            )
            n._settings = s  # CrateNode does not hold its settings
            self._add_log_consumer(n)
            self._on_stop.append(n)
            return (n, version_tuple)
        self._new_node = new_node

    def tearDown(self):
        self._crate_logs_on_failure()
        self._process_on_stop()
        for tmp in self.tmpdirs:
            if DEBUG:
                print(f'# Removing temporary directory {tmp}')
            shutil.rmtree(tmp, ignore_errors=True)
        self.tmpdirs.clear()

    def _process_on_stop(self):
        for n in self._on_stop:
            n.stop()
        self._on_stop.clear()

    def _add_log_consumer(self, node: CrateNode):
        buffer = LineBuffer()
        node.monitor.consumers.append(buffer)
        self._log_consumers.append((node, buffer))

    def _crate_logs_on_failure(self):
        for node, buffer in self._log_consumers:
            node.monitor.consumers.remove(buffer)
            if self._has_error():
                print_error('=' * 70)
                print_error('CrateDB logs for test ' + self.id())
                print_error('-' * 70)
                for line in buffer.lines:
                    print_error(line)
                print_error('-' * 70)
        self._log_consumers.clear()

    def _has_error(self) -> bool:
        return any(error for (_, error) in self._outcome.errors)
