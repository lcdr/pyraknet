import unittest
from unittest.mock import Mock, patch

from event_dispatcher import EventDispatcher

from pyraknet.messages import Message
from pyraknet.server import Server
from pyraknet.transports.abc import Connection, ConnectionEvent, Reliability

class TestConnection(Connection):
	def get_address(self):
		return ("127.0.0.1", 12345)

	def close(self):
		pass

	def _send(self, data, reliability):
		pass

class ServerTest(unittest.TestCase):
	def setUp(self):
		self.dispatcher = EventDispatcher()
		with patch("time.perf_counter", return_value=12345.67):
			self.server = Server(("localhost", 1234), 10, b"test", self.dispatcher)
		self.conn = TestConnection(self.dispatcher)
		self.listener = Mock()

class PacketTest(ServerTest):
	def test_dispatch_user_packet(self):
		self.dispatcher.add_listener(Message.UserPacket, self.listener)
		self.dispatcher.dispatch(ConnectionEvent.Receive, b"\x53test", self.conn)
		self.listener.assert_called_once_with(b"test", self.conn)

	def test_connection_request_ok(self):
		self.dispatcher.add_listener(ConnectionEvent.Send, self.listener)
		self.dispatcher.dispatch(ConnectionEvent.Receive, b"\x04test", self.conn)
		self.listener.assert_called_once_with(b"\x0e\x7f\x00\x00\x01\x39\x30\x00\x00\x7f\x00\x00\x01\xd2\x04", self.conn)

	def test_internal_ping(self):
		self.dispatcher.add_listener(ConnectionEvent.Send, self.listener)
		with patch("time.perf_counter", return_value=23456.78):
			self.dispatcher.dispatch(ConnectionEvent.Receive, b"\x00\xba\xad\xf0\x0d", self.conn)
		self.listener.assert_called_once_with(b"\x03\xba\xad\xf0\x0d\xc6\x8a\xa9\x00", self.conn)
