import asyncio
from ctypes import c_ubyte, c_ushort

from .bitstream import BitStream, c_bit
from .msgIDs import MsgID

class ReplicaManager:
	def __init__(self):
		self._participants = set()
		self._network_ids = {}
		self._objects = {}
		self._current_network_id = 0
		asyncio.async(self._serialize_loop())
		self.register_for_raknet_packet(MsgID.ReplicaManagerConstruction, self.on_construction)
		self.register_for_raknet_packet(MsgID.ReplicaManagerDestruction, self.on_destruction)
		self.register_for_raknet_packet(MsgID.ReplicaManagerSerialize, self.on_serialize)

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
		out.write(c_ubyte(MsgID.ReplicaManagerConstruction))
		out.write(c_bit(True))
		out.write(c_ushort(self._network_ids[obj]))
		out.write(obj.send_construction())

		for recipient in recipients:
			self.send(out, recipient)

	def serialize(self, obj):
		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerSerialize))
		out.write(c_ushort(self._network_ids[obj]))
		out.write(obj.serialize())

		for participant in self._participants:
			self.send(out, participant)

	def destruct(self, obj):
		print("destructing", obj)
		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerDestruction))
		out.write(c_ushort(self._network_ids[obj]))

		for participant in self._participants:
			self.send(out, participant)

		del self._network_ids[obj]

	def _serialize_loop(self):
		while True:
			for obj in self._network_ids:
				if obj._serialize:
					self.serialize(obj)
					obj._serialize = False
			yield from asyncio.sleep(0.3)

	def on_construction(self, construction, address):
		if address in self._participants:
			assert construction.read(c_bit)
			network_id = construction.read(c_ushort)
			self._objects[network_id] = self.on_replica_manager_construction(construction)

	def on_serialize(self, serialize, address):
		if address in self._participants:
			network_id = serialize.read(c_ushort)
			self._objects[network_id].deserialize(serialize)

	def on_destruction(self, destruction, address):
		if address in self._participants:
			network_id = destruction.read(c_ushort)
			del self._objects[network_id]
