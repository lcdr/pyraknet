"""
System for automatically broadcasting object creation, destruction and data updates to connected players.
See RakNet's ReplicaManager.
"""

import logging
from abc import ABC, abstractmethod

from .bitstream import c_bit, c_ubyte, c_ushort, WriteStream
from .messages import Message
from .server import Server

log = logging.getLogger(__name__)

class Replica(ABC):
	"""Abstract base class for replicas (objects serialized using the replica manager system)."""

	@abstractmethod
	def write_construction(self) -> WriteStream:
		"""
		This is where the object should write data to be sent on construction.
		Return the bitstream you wrote to.
		"""

	@abstractmethod
	def serialize(self) -> WriteStream:
		"""
		This is where the object should write data to be sent on serialization.
		Return the bitstream you wrote to.
		"""

	def on_destruction(self) -> None:
		"""
		This will be called by the ReplicaManager before the destruction message is sent.
		"""

class ReplicaManager:
	"""
	Handles broadcasting updates of objects to connected players.
	"""

	def __init__(self, server: Server):
		self._server = server
		self._server.add_handler("disconnect_or_connection_lost", self._on_disconnect_or_connection_lost)
		self._participants = set()
		self._network_ids = {}
		self._current_network_id = 0

	def add_participant(self, address):
		"""
		Add a participant to which object updates will be broadcast to.
		Updates won't automatically be sent to all connected players, just the ones added via this method.
		Disconnected players will automatically be removed from the list when they disconnect.
		Newly added players will receive construction messages for all objects are currently registered with the manager (construct has been called and destruct hasn't been called yet).
		"""
		self._participants.add(address)
		for obj in self._network_ids:
			self._construct(obj, new=False, recipients=(address,))

	def construct(self, obj: Replica, new=True):
		"""
		Send a construction message to participants.

		The object is registered and participants joining later will also receive a construction message when they join (if the object hasn't been destructed in the meantime).
		The actual content of the construction message is determined by the object's write_construction method.
		"""
		self._construct(obj, new)

	def _construct(self, obj: "Replica", new=True, recipients=None):
		# recipients is needed to send replicas to new participants
		if recipients is None:
			recipients = self._participants

		if new:
			self._network_ids[obj] = self._current_network_id
			self._current_network_id += 1

		out = WriteStream()
		out.write(c_ubyte(Message.ReplicaManagerConstruction))
		out.write(c_bit(True))
		out.write(c_ushort(self._network_ids[obj]))
		out.write(obj.write_construction())

		for recipient in recipients:
			self._server.send(out, recipient)

	def serialize(self, obj: Replica):
		"""
		Send a serialization message to participants.

		The actual content of the construction message is determined by the object's serialize method.
		Note that the manager does not automatically send a serialization message when some part of your object changes - you have to call this function explicitly.
		"""
		out = WriteStream()
		out.write(c_ubyte(Message.ReplicaManagerSerialize))
		out.write(c_ushort(self._network_ids[obj]))
		out.write(obj.serialize())

		for participant in self._participants:
			self._server.send(out, participant)

	def destruct(self, obj: Replica):
		"""
		Send a destruction message to participants.

		Before the message is actually sent, the object's on_destruction method is called.
		This message also deregisters the object from the manager so that it won't be broadcast afterwards.
		"""
		log.debug("destructing %s", obj)
		obj.on_destruction()
		out = WriteStream()
		out.write(c_ubyte(Message.ReplicaManagerDestruction))
		out.write(c_ushort(self._network_ids[obj]))

		for participant in self._participants:
			self._server.send(out, participant)

		del self._network_ids[obj]

	def _on_disconnect_or_connection_lost(self, address):
		self._participants.discard(address)
