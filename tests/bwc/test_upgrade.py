import os
import shutil
import unittest
import time
from typing import NamedTuple, Iterable
from io import BytesIO
from crate.client import connect
from crate.client.exceptions import ProgrammingError
from crate.qa.tests import VersionDef, NodeProvider, \
    wait_for_active_shards, insert_data, gen_id

UPGRADE_PATHS = (
    (
        VersionDef('2.0.x', False),
        VersionDef('2.1.x', False),
        VersionDef('2.2.x', False),
        VersionDef('2.3.x', True),
        VersionDef('3.0.x', False),
        VersionDef('3.1.x', False),
        VersionDef('3.1', False),
        VersionDef('3.2.x', False),
        VersionDef('latest-nightly', False),
    ),
)


CREATE_DOC_TABLE = '''
CREATE TABLE t1 (
    id INTEGER PRIMARY KEY,
    col_bool BOOLEAN,
    col_byte BYTE,
    col_short SHORT,
    col_int INTEGER,
    col_long LONG,
    col_float FLOAT,
    col_double DOUBLE,
    col_string STRING,
    col_geo_point GEO_POINT,
    col_geo_shape GEO_SHAPE,
    col_ip IP,
    col_timestamp TIMESTAMP,
    text STRING,
    INDEX text_ft USING FULLTEXT(text) WITH (analyzer=myanalysis)
) CLUSTERED INTO 3 SHARDS WITH (number_of_replicas = 0)
'''

CREATE_BLOB_TABLE = '''
CREATE BLOB TABLE b1
CLUSTERED INTO 3 SHARDS WITH (number_of_replicas = 0)
'''

CREATE_ANALYZER = '''
CREATE ANALYZER myanalysis (
  TOKENIZER whitespace,
  TOKEN_FILTERS (lowercase, kstem),
  CHAR_FILTERS (mymapping WITH (
    type = 'mapping',
    mappings = ['ph=>f', 'qu=>q', 'foo=>bar']
  ))
)
'''


class Statement(NamedTuple):
    stmt: str
    unsupported_versions: Iterable[str]


# Use statements that use different code paths to retrieve the values
SELECT_STATEMENTS = (
    Statement('SELECT _id, _uid, * FROM t1', []),
    Statement('SELECT * FROM t1 WHERE id = 1', []),
    Statement('SELECT * FROM t1 WHERE col_ip > \'127.0.0.1\'', []),
    Statement('''
    SELECT
        COUNT(DISTINCT col_byte),
        COUNT(DISTINCT col_short),
        COUNT(DISTINCT col_int),
        COUNT(DISTINCT col_long),
        COUNT(DISTINCT col_float),
        COUNT(DISTINCT col_double),
        COUNT(DISTINCT col_string),
        COUNT(DISTINCT col_timestamp)
    FROM t1
    ''', []),
    Statement(
        'SELECT COUNT(DISTINCT col_ip) FROM t1',
        ['2.0.x', '2.1.x']
    ),
    Statement('SELECT id, distance(col_geo_point, [0.0, 0.0]) FROM t1', []),
    Statement('SELECT * FROM t1 WHERE within(col_geo_point, col_geo_shape)', []),
    Statement('SELECT date_trunc(\'week\', col_timestamp), sum(col_int), avg(col_float) FROM t1 GROUP BY 1', []),
    Statement('SELECT _score, text FROM t1 WHERE match(text_ft, \'fase\')', []),
    Statement('UPDATE t1 SET col_int = col_int + 1', []),
)


def run_selects(c, version):
    for stmt in SELECT_STATEMENTS:
        if version in stmt.unsupported_versions:
            continue
        try:
            c.execute(stmt.stmt)
        except ProgrammingError as e:
            raise ProgrammingError('Error executing ' + stmt.stmt) from e


def get_test_paths():
    """
    Generater for all possible upgrade paths that should be tested.
    """
    for path in UPGRADE_PATHS:
        for versions in (path[x:] for x in range(len(path) - 1)):
            yield versions


def path_repr(path):
    """
    String representation of the upgrade path in the format::

        from_version -> to_version
    """
    versions = [v for v, _ in path]
    return f'{versions[0]} -> {versions[-1]}'


class StorageCompatibilityTest(NodeProvider, unittest.TestCase):

    CLUSTER_SETTINGS = {
        'cluster.name': gen_id(),
    }

    def test_upgrade_paths(self):
        for path in get_test_paths():
            with self.subTest(path_repr(path)):
                try:
                    self.setUp()
                    self._test_upgrade_path(path, nodes=3)
                finally:
                    self.tearDown()

    def _upgrade(self, cursor, upgrade_segments, num_retries=3):
        """
        Performs the upgrade of the indices and retries in case of
        ProgrammingErrors.

        The retry was added because the wait_for_active shards check
        collects the shard information directly from the nodes. The
        internal ES code, however, retrieves the shard information
        from the ClusterState. A retry is necessary in case the shards
        are ready but the cluster state hasn't been updated yet.
        """
        try:
            if upgrade_segments:
                cursor.execute('OPTIMIZE TABLE doc.t1 WITH (upgrade_segments = true)')
                cursor.execute('OPTIMIZE TABLE blob.b1 WITH (upgrade_segments = true)')
        except ProgrammingError as e:
            print(f'OPTIMIZE failed: {e.message} (num_retries={num_retries})')
            if num_retries > 0 and "PrimaryMissingActionException" in e.message:
                time.sleep(1 / (num_retries + 1))
                self._upgrade(cursor, upgrade_segments, num_retries - 1)
            else:
                raise e

    def _test_upgrade_path(self, versions, nodes):
        """ Test upgrade path across specified versions.

        Creates a blob and regular table in first version and inserts a record,
        then goes through all subsequent versions - each time verifying that a
        few simple selects work.
        """
        cluster = self._new_cluster(versions[0][0], nodes, self.CLUSTER_SETTINGS)
        cluster.start()
        digest = None
        with connect(cluster.node().http_url, error_trace=True) as conn:
            c = conn.cursor()
            c.execute(CREATE_ANALYZER)
            c.execute(CREATE_DOC_TABLE)
            c.execute('''
                INSERT INTO t1 (id, text) VALUES (0, 'Phase queue is foo!')
            ''')
            insert_data(conn, 'doc', 't1', 10)
            c.execute(CREATE_BLOB_TABLE)
            run_selects(c, versions[0].version)
            container = conn.get_blob_container('b1')
            digest = container.put(BytesIO(b'sample data'))
            container.get(digest)
        self._process_on_stop()

        for version, upgrade_segments in versions[1:]:
            self.assert_data_persistence(version, nodes, upgrade_segments, digest)

        # restart with latest version
        version, upgrade_segments = versions[-1]
        self.assert_data_persistence(version, nodes, upgrade_segments, digest)

    def assert_data_persistence(self, version, nodes, upgrade_segments, digest):
        cluster = self._new_cluster(version, nodes, self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()
            wait_for_active_shards(cursor, 6)
            self._upgrade(cursor, upgrade_segments)
            cursor.execute('ALTER TABLE doc.t1 SET ("refresh_interval" = 4000)')
            run_selects(cursor, version)
            container = conn.get_blob_container('b1')
            container.get(digest)
            cursor.execute('ALTER TABLE doc.t1 SET ("refresh_interval" = 2000)')
        self._process_on_stop()


class MetaDataCompatibilityTest(NodeProvider, unittest.TestCase):

    CLUSTER_SETTINGS = {
        'license.enterprise': 'true',
        'lang.js.enabled': 'true',
        'cluster.name': gen_id(),
    }

    SUPPORTED_VERSIONS = (
        '2.3.x',
        'latest-nightly',
    )

    def test_metadata_compatibility(self):
        nodes = 3

        cluster = self._new_cluster(self.SUPPORTED_VERSIONS[0],
                                    nodes,
                                    self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE USER user_a;
            ''')
            cursor.execute('''
                GRANT ALL PRIVILEGES ON SCHEMA doc TO user_a;
            ''')
            cursor.execute('''
                CREATE FUNCTION fact(LONG)
                RETURNS LONG
                LANGUAGE JAVASCRIPT
                AS 'function fact(a) { return a < 2 ? 0 : a * (a - 1); }';
            ''')
        self._process_on_stop()

        for version in self.SUPPORTED_VERSIONS[1:]:
            self.assert_meta_data(version, nodes)

        # restart with latest version
        self.assert_meta_data(self.SUPPORTED_VERSIONS[-1], nodes)

    def assert_meta_data(self, version, nodes):
        cluster = self._new_cluster(version,
                                    nodes,
                                    self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, superuser
                FROM sys.users
                ORDER BY superuser, name;
            ''')
            rs = cursor.fetchall()
            self.assertEqual(['user_a', False], rs[0])
            self.assertEqual(['crate', True], rs[1])
            cursor.execute('''
                SELECT fact(100);
            ''')
            self.assertEqual(9900, cursor.fetchone()[0])
            cursor.execute('''
                SELECT class, grantee, ident, state, type
                FROM sys.privileges
                ORDER BY class, grantee, ident, state, type
            ''')
            self.assertEqual([['SCHEMA', 'user_a', 'doc', 'GRANT', 'DDL'],
                              ['SCHEMA', 'user_a', 'doc', 'GRANT', 'DML'],
                              ['SCHEMA', 'user_a', 'doc', 'GRANT', 'DQL']],
                             cursor.fetchall())

            self._process_on_stop()


class DefaultTemplateMetaDataCompatibilityTest(NodeProvider, unittest.TestCase):
    CLUSTER_ID = gen_id()

    CLUSTER_SETTINGS = {
        'cluster.name': CLUSTER_ID,
        'es.api.enabled': 'true'
    }

    SUPPORTED_VERSIONS = (
        '2.1.x',
        'latest-nightly',
    )

    def test_metadata_compatibility(self):
        nodes = 3

        cluster = self._new_cluster(self.SUPPORTED_VERSIONS[0],
                                    nodes,
                                    self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()
            cursor.execute("select 1")
        self._process_on_stop()

        for version in self.SUPPORTED_VERSIONS[1:]:
            self.assert_dynamic_string_detection(version, nodes)

    def assert_dynamic_string_detection(self, version, nodes):
        """ Test that a dynamic string column detection works as expected.

        If the cluster was initially created/started with a lower CrateDB
        version, we must ensure that our default template is also upgraded, if
        needed, because it is persisted in the cluster state. That's why
        re-creating tables would not help.
        """
        self._move_nodes_folder_if_needed()
        cluster = self._new_cluster(version,
                                    nodes,
                                    self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()
            cursor.execute('CREATE TABLE t1 (o object)')
            cursor.execute('''INSERT INTO t1 (o) VALUES ({"name" = 'foo'})''')
            self.assertEqual(cursor.rowcount, 1)
            cursor.execute('REFRESH TABLE t1')
            cursor.execute("SELECT o['name'], count(*) FROM t1 GROUP BY 1")
            rs = cursor.fetchall()
            self.assertEqual(['foo', 1], rs[0])
            cursor.execute('DROP TABLE t1')
            self._process_on_stop()

    def _move_nodes_folder_if_needed(self):
        """Eliminates the cluster-id folder inside the data directory."""
        data_path_incl_cluster_id = os.path.join(self._path_data, self.CLUSTER_ID)
        if os.path.exists(data_path_incl_cluster_id):
            src_path_nodes = os.path.join(data_path_incl_cluster_id, 'nodes')
            target_path_nodes = os.path.join(self._path_data, 'nodes')
            shutil.move(src_path_nodes, target_path_nodes)
            shutil.rmtree(data_path_incl_cluster_id)


class TableSettingsCompatibilityTest(NodeProvider, unittest.TestCase):

    CLUSTER_SETTINGS = {
        'cluster.name': gen_id(),
    }

    SUPPORTED_VERSIONS = (
        '2.3.x',
        'latest-nightly',
    )

    def test_altering_tables_with_old_settings(self):
        """ Test that the settings of tables created with an old not anymore supported setting can still be changed
        when running with the latest version.
        This test ensures that old settings are removed on upgrade or at latest when changing some table settings.
        Before 3.1.2, purging old settings was not done correctly and thus altering settings of such tables failed.
        """

        nodes = 3

        cluster = self._new_cluster(self.SUPPORTED_VERSIONS[0],
                                    nodes,
                                    self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()

            # The used setting is only valid until version 2.3.x
            cursor.execute('''
                CREATE TABLE t1 (id int) clustered into 4 shards with ("recovery.initial_shards"=1, number_of_replicas=0);
            ''')
            cursor.execute('''
                CREATE TABLE p1 (id int, p int) clustered into 4 shards partitioned by (p) with ("recovery.initial_shards"=1, number_of_replicas=0);
            ''')
            cursor.execute('''
                INSERT INTO p1 (id, p) VALUES (1, 1);
            ''')
        self._process_on_stop()

        for version in self.SUPPORTED_VERSIONS[1:]:
            self.start_cluster_and_alter_tables(version, nodes)

    def start_cluster_and_alter_tables(self, version, nodes):
        cluster = self._new_cluster(version,
                                    nodes,
                                    self.CLUSTER_SETTINGS)
        cluster.start()
        with connect(cluster.node().http_url, error_trace=True) as conn:
            cursor = conn.cursor()
            wait_for_active_shards(cursor, 8)
            cursor.execute('''
                ALTER TABLE t1 SET (number_of_replicas=1)
            ''')
            cursor.execute('''
                ALTER TABLE p1 SET (number_of_replicas=1)
            ''')
        self._process_on_stop()
