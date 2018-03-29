import socket
import unittest
from unittest.mock import Mock

from pyraknet.bitstream import c_ubyte, c_ushort, WriteStream
from pyraknet.messages import Message
from pyraknet.server import Event, PacketReliability, Server

class ServerTest(unittest.TestCase):
	ADDRESS = "127.0.0.1", 1234

	def setUp(self):
		self.server = Server(("localhost", 0), 10, b"test")

class BaseConnectionTest(ServerTest):
	def setUp(self):
		super().setUp()
		self.server._transport = Mock()

	def send_open_connection_request(self):
		self.server.datagram_received(bytes([Message.OpenConnectionRequest, 0]), self.ADDRESS)

	def assert_direct_sent(self, expected):
		self.server._transport.sendto.assert_called_once_with(expected, self.ADDRESS)

	def test_open_connection_request(self):
		self.send_open_connection_request()
		self.assert_direct_sent(bytes([Message.OpenConnectionReply, 0]))

class ConnectionTest(BaseConnectionTest):
	def setUp(self):
		super().setUp()
		BaseConnectionTest.send_open_connection_request(self)
		self.server._transport.reset_mock()
		self.send_mock = self.server._connected[self.ADDRESS].send = Mock()


	def assert_sent(self, expected, rel):
		self.send_mock.assert_called_once_with(expected, rel)

	def assert_sent_something(self):
		self.send_mock.assert_called_once()

	def assert_nothing_sent(self):
		self.send_mock.assert_not_called()

	def send_connection_request(self):
		stream = WriteStream()
		stream.write(c_ubyte(Message.ConnectionRequestAccepted))
		stream.write(socket.inet_aton(self.ADDRESS[0]))
		stream.write(c_ushort(self.ADDRESS[1]))
		stream.write(bytes(2))
		stream.write(socket.inet_aton(self.server._address[0]))
		stream.write(c_ushort(self.server._address[1]))
		self.server._on_packet(bytes([Message.ConnectionRequest])+b"test", self.ADDRESS)
		return stream

	def test_connection_request(self):
		stream = self.send_connection_request()
		self.assert_sent(bytes(stream), PacketReliability.Reliable)

class MiscTest(ConnectionTest):
	def setUp(self):
		super().setUp()
		ConnectionTest.send_connection_request(self)
		self.send_mock.reset_mock()

	def test_handle_user_packet(self):
		mock = Mock()
		self.server.add_handler(Event.UserPacket, mock)
		self.server._on_packet(bytes([Message.UserPacket]), self.ADDRESS)
		mock.assert_called_once()

	def test_close_connection(self):
		mock = Mock()
		self.server.add_handler(Event.Disconnect, mock)
		self.server.close_connection(self.ADDRESS)
		self.assert_sent(bytes([Message.DisconnectionNotification]), PacketReliability.ReliableOrdered)
		mock.assert_called_once()
