import asyncio
import os.path
import time
import unittest
from unittest.mock import Mock

from event_dispatcher import EventDispatcher

from pyraknet.transports.abc import Connection, ConnectionEvent, Reliability
from pyraknet.transports.raknet.connection import RaknetConnection

time.perf_counter = lambda *args, **kwargs: 0

class ConnectionTest(unittest.TestCase):
	ADDRESS = "127.0.0.1", 1234

	def setUp(self):
		self.transport = Mock()
		self.dispatcher = EventDispatcher()
		self.conn = RaknetConnection(self.transport, self.dispatcher, self.ADDRESS)
		self.listener = Mock()
		self.dispatcher.add_listener(ConnectionEvent.Receive, self.listener)

	def test_parse_ping(self):
		self.conn.handle_datagram(b"\x41\x86\xc4\x40\x1e\x80\x00\x00\x12\x28\x00\x06\x1b\x11\x00")
		self.listener.assert_called_once_with(b"\x00\x06\x1b\x11\x00", self.conn)
		asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.03))
		self.transport.sendto.assert_called_once_with(b"\x83\x0d\x88\x80\x63\x7a\x00\x00\x00", self.ADDRESS)

	def test_parse_acks(self):
		self.conn.handle_datagram(b"\xba\x6e\x04\x00\x63\x78\x00\x00\x00\x00")
		self.listener.assert_not_called()

	def test_parse_split_packet(self):
		with open(os.path.join(__file__, "..", "res/in_split1.bin"), "rb") as file:
			dgram1 = file.read()
		with open(os.path.join(__file__, "..", "res/in_split2.bin"), "rb") as file:
			dgram2 = file.read()
		with open(os.path.join(__file__, "..", "res/in_split3.bin"), "rb") as file:
			dgram3 = file.read()
		with open(os.path.join(__file__, "..", "res/in_payload.bin"), "rb") as file:
			payload = file.read()

		self.conn.handle_datagram(dgram1)
		self.listener.assert_not_called()
		self.conn.handle_datagram(dgram2)
		self.listener.assert_not_called()
		self.conn.handle_datagram(dgram3)
		self.listener.assert_called_once_with(payload, self.conn)

	def test_send_split_packet(self):
		with open(os.path.join(__file__, "..", "res/out_payload.bin"), "rb") as file:
			payload = file.read()
		with open(os.path.join(__file__, "..", "res/out_split1.bin"), "rb") as file:
			dgram1 = file.read()
		with open(os.path.join(__file__, "..", "res/out_split2.bin"), "rb") as file:
			dgram2 = file.read()
		with open(os.path.join(__file__, "..", "res/out_split3.bin"), "rb") as file:
			dgram3 = file.read()

		self.conn._packets_sent = -10 # otherwise	 packets won't actually be sent
		self.conn.send(payload, Reliability.ReliableOrdered)
		self.transport.sendto.assert_any_call(dgram1, self.ADDRESS)
		self.transport.sendto.assert_any_call(dgram2, self.ADDRESS)
		self.transport.sendto.assert_any_call(dgram3, self.ADDRESS)

