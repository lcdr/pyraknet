from enum import auto, Enum
from typing import Container, SupportsBytes

from event_dispatcher import EventDispatcher

from ..messages import Address

class TransportEvent(Enum):
	NetworkInit = auto()

class Reliability(Enum):
	Unreliable = 0
	UnreliableSequenced = 1
	Reliable = 2
	ReliableOrdered = 3
	ReliableSequenced = 4

class ConnectionEvent(Enum):
	Receive = auto()
	Send = auto()
	Broadcast = auto()
	Close = auto()

class ConnectionType(Enum):
	RakNet = auto()
	TcpUdp = auto()

class Connection:
	def __init__(self, dispatcher):
		self._dispatcher = dispatcher
		self._dispatcher.add_listener(ConnectionEvent.Broadcast, self._on_broadcast)

	def get_type(self) -> ConnectionType:
		raise NotImplementedError

	def get_addr(self) -> Address:
		raise NotImplementedError

	def close(self) -> None:
		raise NotImplementedError

	def send(self, data: SupportsBytes, reliability: Reliability=Reliability.ReliableOrdered) -> None:
		data = bytes(data)
		self._dispatcher.dispatch(ConnectionEvent.Send, data, self)
		self._send(data, reliability)

	def _send(self, data: bytes, reliability: Reliability) -> None:
		raise NotImplementedError

	def broadcast(self, data: SupportsBytes, reliability: Reliability=Reliability.ReliableOrdered, exclude: Container["Connection"]=()) -> None:
		data = bytes(data)
		self._dispatcher.dispatch(ConnectionEvent.Broadcast, data, reliability, exclude)

	def _on_broadcast(self, data: bytes, reliability: Reliability, exclude: Container["Connection"]=()) -> None:
		if self not in exclude:
			self.send(data, reliability)
