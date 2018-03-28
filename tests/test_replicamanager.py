from pyraknet.replicamanager import Replica, ReplicaManager

from pyraknet.tests.test_server import ConnectionTest
""""""
class TestReplica(Replica):
	def write_construction(self, stream):
		stream.write(b"construction")

	def serialize(self, stream):
		stream.write(b"serialize")

class ReplicaManagerTest(ConnectionTest):
	def setUp(self):
		super().setUp()
		self.replica_manager = ReplicaManager(self.server)
		self.replica = TestReplica()
		self.replica_manager.add_participant(self.ADDRESS)

	def test_construction(self):
		self.replica_manager.construct(self.replica)
		self.assert_sent_something()

	def test_disconnect(self):
		self.server._on_disconnect_or_connection_lost(self.ADDRESS)
		self.replica_manager.construct(self.replica)
		self.assert_nothing_sent()

class ReplicaManagerParticipantTest(ConnectionTest):
	def setUp(self):
		super().setUp()
		self.replica_manager = ReplicaManager(self.server)
		self.replica = TestReplica()

	def test_delayed_add(self):
		self.replica_manager.construct(self.replica)
		self.assert_nothing_sent()
		self.replica_manager.add_participant(self.ADDRESS)
		self.assert_sent_something()

class ReplicaTest(ReplicaManagerTest):
	def setUp(self):
		super().setUp()
		super().test_construction()
		self.send_mock.reset_mock()

	def test_serialize(self):
		self.replica_manager.serialize(self.replica)
		self.assert_sent_something()

	def test_destruct(self):
		self.replica_manager.destruct(self.replica)
		self.assert_sent_something()

		with self.assertRaises(KeyError):
			self.replica_manager.serialize(self.replica)
		with self.assertRaises(KeyError):
			self.replica_manager.destruct(self.replica)
