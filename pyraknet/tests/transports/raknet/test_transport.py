
# todo

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

def test_close_connection(self):
		mock = Mock()
		self.server.add_handler(Event.Disconnect, mock)
		self.server.close_connection(self.ADDRESS)
		self.assert_sent(bytes([Message.DisconnectionNotification]), Reliability.ReliableOrdered)
		mock.assert_called_once()
