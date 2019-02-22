from pyraknet.replicamanager import Replica, ReplicaManager
from pyraknet.messages import Message
from pyraknet.transports.abc import ConnectionEvent
from pyraknet.tests.test_server import ServerTest

class TestReplica(Replica):
	def write_construction(self, stream):
		stream.write(b"construction")

	def serialize(self, stream):
		stream.write(b"serialize")

class BaseReplicaManagerTest(ServerTest):
	def setUp(self):
		super().setUp()
		self.replica_manager = ReplicaManager(self.dispatcher)
		self.replica = TestReplica()
		self.dispatcher.add_listener(ConnectionEvent.Send, self.listener)

class ParticipantTest(BaseReplicaManagerTest):
	def setUp(self):
		super().setUp()
		self.replica_manager.add_participant(self.conn)

class ReplicaManagerTest(ParticipantTest):
	def test_construction(self):
		self.replica_manager.construct(self.replica)
		self.listener.assert_called_once_with(b"\x24\x80\x00\x31\xb7\xb79\xba\x39\x3a\xb1\xba4\xb7\xb7\x00", self.conn)

class DelayedAddTest(BaseReplicaManagerTest):
	def setUp(self):
		super().setUp()
		self.replica_manager = ReplicaManager(self.dispatcher)
		self.replica = TestReplica()

	def test_delayed_add(self):
		self.replica_manager.construct(self.replica)
		self.listener.assert_not_called()
		self.replica_manager.add_participant(self.conn)
		self.listener.assert_called_once_with(b"\x24\x80\x00\x31\xb7\xb79\xba\x39\x3a\xb1\xba4\xb7\xb7\x00", self.conn)

class BaseReplicaTest(ParticipantTest):
	def setUp(self):
		super().setUp()
		self.replica_manager.construct(self.replica)
		self.listener.reset_mock()

class ReplicaTest(BaseReplicaTest):
	# todo: test that serialize before construct errors

	def test_serialize(self):
		self.replica_manager.serialize(self.replica)
		self.listener.assert_called_once_with(b"\x27\x00\x00serialize", self.conn)

	def test_destruct(self):
		self.replica_manager.destruct(self.replica)
		self.listener.assert_called_once_with(b"\x25\x00\x00", self.conn)

		with self.assertRaises(KeyError):
			self.replica_manager.serialize(self.replica)
		with self.assertRaises(KeyError):
			self.replica_manager.destruct(self.replica)
