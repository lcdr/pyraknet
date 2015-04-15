# Todo: combine packets in datagrams if possible
import asyncio
import math
import time
from collections import OrderedDict
from ctypes import c_uint, c_ushort

from . import rangelist
from .bitstream import BitStream, c_bit

class PacketReliability:
	Unreliable = 0
	UnreliableSequenced = 1
	Reliable = 2
	ReliableOrdered = 3
	ReliableSequenced = 4

class ReliabilityLayer:
	def __init__(self, transport, address):
		self._transport = transport
		self._address = address
		self._acks = rangelist.RangeList()
		self._send_message_number_index = 0
		self._sequenced_write_index = 0 # currently not used
		self._sequenced_read_index = 0
		self._ordered_write_index = 0
		self._ordered_read_index = 0
		self._last_received = [-1] * 10
		self._out_of_order_packets = {} # for ReliableOrdered
		self._sends = []
		self._resends = OrderedDict()

		asyncio.async(self._send_loop(is_resends=False))
		asyncio.async(self._send_loop(is_resends=True))

	def handle_datagram(self, datagram):
		stream = BitStream(datagram)
		try:
			self.handle_datagram_header(stream)
		except IndexError: # Acks only packet
			return

		# There can be multiple packets in one datagram
		yield from self.parse_packets(stream)

	def handle_datagram_header(self, data):
		has_acks = data.read(c_bit)
		if has_acks:
			some_time_thingy = data.read(c_uint) # seems like this is used to calculate some ping thingy
			acks = rangelist.RangeList(data)
			for message_number in acks.ranges():
				del self._resends[message_number]

		has_remote_system_time = data.read(c_bit)
		if has_remote_system_time:
			remote_system_time = data.read(c_uint) # seems like this is never used in RakNet

	def parse_packets(self, data):
		try:
			while True:
				message_number = data.read(c_uint)
				reliability = data.read_bits(3)[0]
				assert reliability != PacketReliability.ReliableSequenced # This is never used

				if reliability == PacketReliability.UnreliableSequenced or reliability == PacketReliability.ReliableOrdered:
					ordering_channel = data.read_bits(5)[0]
					assert ordering_channel == 0 # No one actually uses a custom ordering channel
					ordering_index = data.read(c_uint)

				is_split_packet = data.read(c_bit)
				if is_split_packet:
					raise NotImplementedError

				length = data.read(c_ushort, compressed=True)
				packet_data = data.read_aligned_bytes(math.ceil(length / 8))

				if message_number not in self._last_received:
					del self._last_received[0]
					self._last_received.append(message_number)

				if reliability == PacketReliability.UnreliableSequenced:
					if ordering_index >= self._sequenced_read_index:
						self._sequenced_read_index = ordering_index + 1
					else:
						continue # the packet is outdated, drop
				elif reliability in (PacketReliability.Reliable, PacketReliability.ReliableOrdered):
					self._acks.append(message_number)
					if reliability == PacketReliability.ReliableOrdered:
						if ordering_index == self._ordered_read_index:
							self._ordered_read_index += 1
							ord = ordering_index+1
							while ord in self._out_of_order_packets:
								self._ordered_read_index += 1
								yield self._out_of_order_packets.pop(ord)
								ord += 1
						elif ordering_index < self._ordered_read_index:
							# We already received this packet (probably a late resend), drop
							raise SystemExit
						else:
							# Packet arrived too early, we're still waiting for a previous packet
							# Add this one to a queue so we can process it later
							self._out_of_order_packets[ordering_index] = packet_data
				yield packet_data
		except IndexError: # No more packets
			return

	@asyncio.coroutine
	def _send_loop(self, is_resends):
		if is_resends:
			queue = self._resends
			interval = 1
		else:
			queue = self._sends
			interval = 0.03

		while True:
			for i in queue:
				if is_resends:
					message_number = i
					data, reliability, ordering_index = queue[i]
				else:
					data, reliability = i
					message_number = self._send_message_number_index
					self._send_message_number_index += 1
					if reliability == PacketReliability.ReliableOrdered:
						ordering_index = self._ordered_write_index
						self._ordered_write_index += 1
					else:
						ordering_index = None

				# I'm not exactly sure what to use here
				remote_system_time = int(time.perf_counter() * 1000)

				out = BitStream()
				out.write(c_bit(len(self._acks) != 0))
				if self._acks:
					out.write(c_uint(remote_system_time))
					out.write(self._acks.serialize())
					self._acks.clear()

				has_remote_system_time = False # seems like time is never actually used
				out.write(c_bit(has_remote_system_time))
				#out.write(c_uint(remote_system_time))

				out.write(c_uint(message_number))

				out.write_bits(reliability.to_bytes(length=1, byteorder="little"), 3)

				if reliability == PacketReliability.ReliableOrdered:
					out.write_bits(b"\0", 5) # ordering_channel, no one ever uses anything else than 0
					out.write(c_uint(ordering_index))

				is_split_packet = False
				out.write(c_bit(is_split_packet))
				out.write(c_ushort(len(data) * 8), compressed=True)
				out.write_aligned_bytes(data)

				out = bytes(out)

				assert len(out) < 1492 # MTU, need to decide what to do when this happens
				# It might be that this assert is not needed, but i'll have to test that when i actually send a large packet
				self._transport.sendto(out, self._address)

				if reliability == PacketReliability.Reliable or reliability == PacketReliability.ReliableOrdered:
					self._resends[message_number] = data, reliability, ordering_index
			if not is_resends:
				self._sends.clear()
			if self._acks:
				out = BitStream()
				out.write(c_bit(True))
				# I'm not exactly sure what to write here
				remote_system_time = int(time.perf_counter() * 1000)
				out.write(c_uint(remote_system_time))
				out.write(self._acks.serialize())
				self._acks.clear()
				self._transport.sendto(bytes(out), self._address)
			yield from asyncio.sleep(interval)
