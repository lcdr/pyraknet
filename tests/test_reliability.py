import os.path
import time
import unittest
from unittest.mock import Mock

from pyraknet._reliability import PacketReliability, ReliabilityLayer

time.perf_counter = lambda *args, **kwargs: 0

class ReliabilityTest(unittest.TestCase):
	ADDRESS = "127.0.0.1", 1234

	def setUp(self):
		self.rel = ReliabilityLayer(Mock(), self.ADDRESS)

	def test_parse_ping(self):
		self.assertEqual(list(self.rel.handle_datagram(b"\x41\x86\xc4\x40\x1e\x80\x00\x00\x12\x28\x00\x06\x1b\x11\x00")), [b'\x00\x06\x1b\x11\x00'])

	def test_parse_acks(self):
		self.assertEqual(list(self.rel.handle_datagram(b"\xba\x6e\x04\x00\x63\x78\x00\x00\x00\x00")), [])

	def test_send_acks(self):
		self.rel._acks.insert(42)
		self.rel._send_loop()
		self.rel._transport.sendto.assert_called_once_with(b"\x80\x00\x00\x00c*\x00\x00\x00", self.ADDRESS)

	def test_parse_split_packet(self):
		with open(os.path.join(__file__, "..", "res/in_split1.bin"), "rb") as file:
			packet1 = file.read()
		with open(os.path.join(__file__, "..", "res/in_split2.bin"), "rb") as file:
			packet2 = file.read()
		with open(os.path.join(__file__, "..", "res/in_split3.bin"), "rb") as file:
			packet3 = file.read()
		with open(os.path.join(__file__, "..", "res/in_payload.bin"), "rb") as file:
			payload = file.read()

		self.assertEqual(list(self.rel.handle_datagram(packet1)), [])
		self.assertEqual(list(self.rel.handle_datagram(packet2)), [])
		self.assertEqual(list(self.rel.handle_datagram(packet3)), [payload])

	def test_send_split_packet(self):
		with open(os.path.join(__file__, "..", "res/out_payload.bin"), "rb") as file:
			payload = file.read()
		with open(os.path.join(__file__, "..", "res/out_split1.bin"), "rb") as file:
			expected1 = file.read()
		with open(os.path.join(__file__, "..", "res/out_split2.bin"), "rb") as file:
			expected2 = file.read()
		with open(os.path.join(__file__, "..", "res/out_split3.bin"), "rb") as file:
			expected3 = file.read()

		self.rel._cwnd = 10 # need to increase or packets won't actually be sent
		self.rel.send(payload, PacketReliability.ReliableOrdered)
		self.rel._send_loop()
		self.rel._transport.sendto.assert_any_call(expected1, self.ADDRESS)
		self.rel._transport.sendto.assert_any_call(expected2, self.ADDRESS)
		self.rel._transport.sendto.assert_any_call(expected3, self.ADDRESS)

