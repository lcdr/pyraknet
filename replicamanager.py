import asyncio
from ctypes import c_ubyte, c_ushort

from .bitstream import BitStream, c_bit
from .msgIDs import MsgID

class ReplicaManager:
	@asyncio.coroutine
	def __init__(self, *args, **kwargs):
		self.participants = set()
		self.objects = set()
		self._current_network_id = 0
		asyncio.async(self._serialize_loop())

	def add_participant(self, address):
		self.participants.add(address)
		for obj in self.objects:
			self.construct(obj, (address,), assign_net_id=False)

	def on_disconnect_or_connection_lost(self, data, address):
		self.participants.discard(address)

	def construct(self, object, recipients=None, assign_net_id=True):
		# recipients is needed to send replicas to new participants
		if recipients is None:
			recipients = self.participants

		if assign_net_id:
			object._network_id = self._current_network_id
			self._current_network_id += 1

		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerConstruction))
		out.write(c_bit(True))
		out.write(c_ushort(object._network_id))
		out.write(object.send_construction())

		for recipient in recipients:
			self.send(out, recipient)

		self.objects.add(object)

	def serialize(self, object):
		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerSerialize))
		out.write(c_ushort(object._network_id))
		out.write(object.serialize())

		for participant in self.participants:
			self.send(out, participant)

	def destruct(self, object):
		print("destructing", object)
		out = BitStream()
		out.write(c_ubyte(MsgID.ReplicaManagerDestruction))
		out.write(c_ushort(object._network_id))

		for participant in self.participants:
			self.send(out, participant)

		self.objects.remove(object)

	def _serialize_loop(self):
		while True:
			for obj in self.objects:
				if obj._serialize:
					self.serialize(obj)
					obj._serialize = False
			yield from asyncio.sleep(0.3)

class Replica:
	def __init__(self, manager):
		print("Game object created")
		self._manager = manager
		self._serialize = False

	def __del__(self):
		print("Game object destroyed")
		self._manager.destruct(self)
