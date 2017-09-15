import logging

from .bitstream import BitStream, c_bit, c_ubyte, c_ushort
from .messages import Message

log = logging.getLogger(__name__)

class ReplicaManager:
	def __init__(self, server):
		self._server = server
		self._server.register_handler(Message.DisconnectionNotification, self.on_disconnect_or_connection_lost)
		self._participants = set()
		self._network_ids = {}
		self._current_network_id = 0

	def add_participant(self, address):
		self._participants.add(address)
		for obj in self._network_ids:
			self.construct(obj, (address,), new=False)

	def on_disconnect_or_connection_lost(self, data, address):
		self._participants.discard(address)

	def construct(self, obj, recipients=None, new=True):
		# recipients is needed to send replicas to new participants
		if recipients is None:
			recipients = self._participants

		if new:
			self._network_ids[obj] = self._current_network_id
			self._current_network_id += 1

		out = BitStream()
		out.write(c_ubyte(Message.ReplicaManagerConstruction))
		out.write(c_bit(True))
		out.write(c_ushort(self._network_ids[obj]))
		out.write(obj.write_construction())

		for recipient in recipients:
			self._server.send(out, recipient)

	def serialize(self, obj):
		out = BitStream()
		out.write(c_ubyte(Message.ReplicaManagerSerialize))
		out.write(c_ushort(self._network_ids[obj]))
		out.write(obj.serialize())

		for participant in self._participants:
			self._server.send(out, participant)

	def destruct(self, obj):
		log.debug("destructing %s", obj)
		obj.on_destruction()
		out = BitStream()
		out.write(c_ubyte(Message.ReplicaManagerDestruction))
		out.write(c_ushort(self._network_ids[obj]))

		for participant in self._participants:
			self._server.send(out, participant)

		del self._network_ids[obj]

class Replica:
	def write_construction(self):
		"""
		This is where the object should write data to be sent on construction.
		Return the bitstream you wrote to.
		"""

	def serialize(self):
		"""
		This is where the object should write data to be sent on serialization.
		Return the bitstream you wrote to.
		"""

	def on_destruction(self):
		"""
		This will be called by the ReplicaManager before the Destruction message is sent.
		"""
