import logging

from event_dispatcher import EventDispatcher

from .messages import Message
from .transports.abc import Connection, ConnectionEvent

log = logging.getLogger(__name__)

class PacketLogger:
	def __init__(self, dispatcher: EventDispatcher, excluded_packets=None):
		dispatcher.add_listener(ConnectionEvent.Receive, self._on_receive_packet)
		dispatcher.add_listener(ConnectionEvent.Send, self._on_send_packet)
		if excluded_packets is None:
			self._excluded_packets = {}
		else:
			self._excluded_packets = excluded_packets

	def _on_receive_packet(self, data: bytes, conn: Connection) -> None:
		self._log_packet(data, True)

	def _on_send_packet(self, data: bytes, conn: Connection) -> None:
		self._log_packet(data, False)

	def _log_packet(self, data: bytes, received: bool) -> None:
		try:
			message = Message(data[0])
			console_log = message not in self._excluded_packets
			packetname = message.name
		except ValueError:
			packetname = "Nonexisting packet %i" % data[0]
			console_log = True

		if console_log:
			if received:
				log.debug("got %s", packetname)
			else:
				log.debug("snd %s", packetname)

