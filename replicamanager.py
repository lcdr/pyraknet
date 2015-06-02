import asyncio
from ctypes import c_ubyte, c_ushort

from .bitstream import BitStream, c_bit
from .msgIDs import MsgID

class ReplicaManager:
	def __init__(self, is_server):
		self.participants = set()
		self.network_ids = {}
		self.objects = {}
		self._current_network_id = 0
		asyncio.async(self._serialize_loop())
		self.register_for_raknet_packet(MsgID.ReplicaManagerConstruction, self.on_construction)
		self.register_for_raknet_packet(MsgID.ReplicaManagerDestruction, self.on_destruction)
		self.register_for_raknet_packet(MsgID.ReplicaManagerSerialize, self.on_serialize)

	def add_participant(self, address):
		self.participants.add(address)
		for obj in self.network_ids:
			self.construct(obj, (address,), new=False)

	def on_disconnect_or_connection_lost(self, data, address):
		self.participants.discard(address)

	def construct(self, object, recipients=None, new=True):
		# recipients is needed to send replicas to new participants
		if recipients is None:
			recipients = self.participants

		if new:
			self.network_ids[object] = self._current_network_id
			self._current_network_id += 1

		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerConstruction))
		out.write(c_bit(True))
		out.write(c_ushort(self.network_ids[object]))
		out.write(object.send_construction())

		for recipient in recipients:
			self.send(out, recipient)

	def serialize(self, object):
		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerSerialize))
		out.write(c_ushort(self.network_ids[object]))
		out.write(object.serialize())

		for participant in self.participants:
			self.send(out, participant)

	def destruct(self, object):
		print("destructing", object)
		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerDestruction))
		out.write(c_ushort(self.network_ids[object]))

		for participant in self.participants:
			self.send(out, participant)

		del self.network_ids[object]

	def _serialize_loop(self):
		while True:
			for obj in self.network_ids:
				if obj._serialize:
					self.serialize(obj)
					obj._serialize = False
			yield from asyncio.sleep(0.3)

	def on_construction(self, construction, address):
		if address in self.participants:
			assert construction.read(c_bit)
			network_id = construction.read(c_ushort)
			self.objects[network_id] = self.on_replica_manager_construction(construction)

	def on_serialize(self, serialize, address):
		if address in self.participants:
			network_id = serialize.read(c_ushort)
			self.objects[network_id].deserialize(serialize)

	def on_destruction(self, destruction, address):
		if address in self.participants:
			network_id = destruction.read(c_ushort)
			del self.objects[network_id]

class Replica:
	def __init__(self, manager):
		print("Game object created")
		self._manager = manager
		self._serialize = False

	def __del__(self):
		print("Game object destroyed")
		self._manager.destruct(self)
