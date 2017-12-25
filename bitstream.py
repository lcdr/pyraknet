"""
Module for sequential reading (ReadStream) and writing (WriteStream) from/to bytes.
Also includes objects for converting datatypes from/to bytes, similar to the standard library struct module.
"""

import math
import struct
from typing import AnyStr, ByteString, SupportsBytes

class _Struct(struct.Struct):
	# cast-like syntax for packing
	def __call__(self, value) -> bytes:
		return self.pack(value)

	def __str__(self) -> str:
		return "<Struct %s>" % self.format

c_bool = _Struct("<?")
c_float = _Struct("<f")
c_double = _Struct("<d")
c_int = _Struct("<i")
c_uint = _Struct("<I")

c_byte = _Struct("<b")
c_ubyte = _Struct("<B")
c_short = _Struct("<h")
c_ushort = _Struct("<H")
c_long = _Struct("<l")
c_ulong = _Struct("<L")
c_longlong = _Struct("<q")
c_ulonglong = _Struct("<Q")

c_int8 = c_byte
c_uint8 = c_ubyte
c_int16 = c_short
c_uint16 = c_ushort
c_int32 = c_long
c_uint32 = c_ulong
c_int64 = c_longlong
c_uint64 = c_ulonglong

class c_bit:
	def __init__(self, boolean):
		self.value = boolean

class _AbstractStream(ByteString, SupportsBytes):
	_data: ByteString

	def __bytes__(self):
		return bytes(self._data)

	def __getitem__(self, item):
		return self._data[item]

	def __len__(self):
		return len(self._data)

	def __str__(self):
		return str(self._data)

class ReadStream(_AbstractStream):
	"""Allows simple sequential reading from bytes."""
	_data: bytes

	def __init__(self, data: bytes):
		self._data = data
		self.read_offset = 0

	def skip_read(self, byte_length: int) -> None:
		"""Skips reading byte_length number of bytes."""
		self.read_offset += byte_length * 8

	def read(self, arg_type, compressed=False, length: int=None, allocated_length: int=None, length_type=None):
		"""
		Read a value of type arg_type from the bitstream.
		allocated_length is for fixed-length strings.
		length_type is for variable-length strings.
		"""
		if isinstance(arg_type, struct.Struct):
			if compressed:
				if arg_type in (c_float, c_double):
					raise NotImplementedError
				read = self._read_compressed(arg_type.size)
			else:
				read = self._read_bytes(arg_type.size)
			return arg_type.unpack(read)[0]
		if issubclass(arg_type, c_bit):
			return self._read_bit()
		if allocated_length is not None or length_type is not None:
			return self._read_str(arg_type, allocated_length, length_type)
		if issubclass(arg_type, bytes):
			return self._read_bytes(length)
		raise TypeError(arg_type)

	def _read_str(self, arg_type, allocated_length: int, length_type) -> AnyStr:
		if issubclass(arg_type, str):
			char_size = 2
		else:
			char_size = 1

		if length_type is not None:
			# Variable-length string
			length = self.read(length_type)
			value = self.read(bytes, length=length*char_size)
		else:
			# Fixed-length string
			value = self._read_bytes(allocated_length*char_size)
			# find null terminator
			for i in range(len(value)):
				char = value[i*char_size:(i+1)*char_size]
				if char == bytes(char_size):
					value = value[:i*char_size]
					break
			else:
				raise RuntimeError("String doesn't have null terminator")

		if issubclass(arg_type, str):
			value = value.decode("utf-16-le")
		return value

	def _read_bit(self) -> bool:
		bit = self._data[self.read_offset // 8] & 0x80 >> self.read_offset % 8 != 0
		self.read_offset += 1
		return bit

	def read_bits(self, number_of_bits: int) -> int:
		assert 0 < number_of_bits < 8

		output = (self._data[self.read_offset // 8] << self.read_offset % 8) & 0xff # First half
		if self.read_offset % 8 != 0 and number_of_bits > 8 - self.read_offset % 8: # If we have a second half, we didn't read enough bytes in the first half
			output |= self._data[self.read_offset // 8 + 1] >> 8 - self.read_offset % 8 # Second half (overlaps byte boundary)
		output >>= 8 - number_of_bits
		self.read_offset += number_of_bits
		return output

	def _read_bytes(self, length: int) -> bytes:
		if self.read_offset % 8 == 0:
			num_bytes_read = length
		else:
			num_bytes_read = length+1

		# check whether there is enough left to read
		if len(self._data) - self.read_offset//8 < num_bytes_read:
			raise EOFError("Trying to read %i bytes but only %i remain" % (num_bytes_read, len(self._data) - self.read_offset // 8))

		output = bytearray(self._data[self.read_offset // 8:self.read_offset // 8 + num_bytes_read])
		if self.read_offset % 8 != 0:
			# data is shifted
			# clear the part before the struct
			output[0] &= (1 << 8 - self.read_offset % 8) - 1
			# shift back
			output = (int.from_bytes(output, "big") >> (8 - self.read_offset % 8)).to_bytes(length, "big")
		self.read_offset += length * 8
		return output

	def _read_compressed(self, number_of_bytes: int) -> bytes:
		current_byte = number_of_bytes - 1

		while current_byte > 0:
			if self._read_bit():
				current_byte -= 1
			else:
				# Read the rest of the bytes
				return bytearray(number_of_bytes - current_byte - 1) + self._read_bytes(current_byte + 1)

		# All but the first bytes are 0. If the upper half of the last byte is a 0 (positive) or 16 (negative) then what we read will be a 1 and the remaining 4 bits.
		# Otherwise we read a 0 and the 8 bits
		if self._read_bit():
			start = bytes([self.read_bits(4)])
		else:
			start = self._read_bytes(1)
		return start + bytearray(number_of_bytes - current_byte - 1)

	def align_read(self) -> None:
		if self.read_offset % 8 != 0:
			self.read_offset += 8 - self.read_offset % 8

	def all_read(self) -> bool:
		# This is not accurate to the bit, just to the byte
		return math.ceil(self.read_offset / 8) == len(self._data)

# Note: a ton of the logic here assumes that the write offset is never moved back, that is, that you never overwrite things
# Doing so may break everything
class WriteStream(_AbstractStream):
	"""Allows simple sequential writing to bytes."""
	_data: bytearray

	def __init__(self):
		self._data = bytearray()
		self._write_offset = 0

	def write(self, arg, compressed: bool=False, allocated_length: int=None, length_type=None) -> None:
		"""
		Write a value to the bitstream.
		allocated_length is for fixed-length strings.
		length_type is for variable-length strings.
		"""
		if isinstance(arg, WriteStream):
			self._write_bytes(arg._data)
			if arg._write_offset % 8 != 0:
				# this should work assuming the part after the arg's write offset is completely 0
				self._write_offset -= 8 - arg._write_offset % 8
				# in some cases it's possible we've written an unnecessary byte
				if self._write_offset//8 == len(self._data)-2:
					del self._data[-1]
			return
		if allocated_length is not None or length_type is not None:
			self._write_str(arg, allocated_length, length_type)
			return
		if isinstance(arg, (bytes, bytearray)):
			if compressed:
				self._write_compressed(arg)
			else:
				self._write_bytes(arg)
			return
		if isinstance(arg, c_bit):
			self._write_bit(arg.value)
			return

		raise TypeError(arg)

	def _write_str(self, str_: AnyStr, allocated_length: int, length_type) -> None:
		# possibly include default encoded length for non-variable-length strings (seems to be 33)
		if isinstance(str_, str):
			encoded_str = str_.encode("utf-16-le")
		else:
			encoded_str = str_

		if length_type is not None:
			# Variable-length string
			self.write(length_type(len(str_))) # note: there's also a version that uses the length of the encoded string, should that be used?
		else:
			# Fixed-length string
			# null terminator
			if isinstance(str_, str):
				char_size = 2
			else:
				char_size = 1

			if len(str_)+1 > allocated_length:
				raise ValueError("String too long!")
			encoded_str += bytes(allocated_length*char_size-len(encoded_str))
		self._write_bytes(encoded_str)

	def _write_bit(self, bit: bool) -> None:
		self._alloc_bits(1)
		if bit: # we don't actually have to do anything if the bit is 0
			self._data[self._write_offset//8] |= 0x80 >> self._write_offset % 8

		self._write_offset += 1

	def write_bits(self, value: int, number_of_bits: int) -> None:
		assert 0 < number_of_bits < 8
		self._alloc_bits(number_of_bits)

		if number_of_bits < 8: # In the case of a partial byte, the bits are aligned from the right (bit 0) rather than the left (as in the normal internal representation)
			value = value << (8 - number_of_bits) & 0xff # Shift left to get the bits on the left, as in our internal representation
		if self._write_offset % 8 == 0:
			self._data[self._write_offset//8] = value
		else:
			self._data[self._write_offset//8] |= value >> self._write_offset % 8 # First half
			if 8 - self._write_offset % 8 < number_of_bits: # If we didn't write it all out in the first half (8 - self._write_offset % 8 is the number we wrote in the first half)
				self._data[self._write_offset//8 + 1] = (value << 8 - self._write_offset % 8) & 0xff # Second half (overlaps byte boundary)

		self._write_offset += number_of_bits

	def _write_bytes(self, byte_arg: bytes) -> None:
		if self._write_offset % 8 == 0:
			self._data[self._write_offset//8:self._write_offset//8+len(byte_arg)] = byte_arg
		else:
			# shift new input to current shift
			new = (int.from_bytes(byte_arg, "big") << (8 - self._write_offset % 8)).to_bytes(len(byte_arg)+1, "big")
			# update current byte
			self._data[self._write_offset//8] |= new[0]
			# add rest
			self._data[self._write_offset//8+1:self._write_offset//8+1+len(byte_arg)] = new[1:]
		self._write_offset += len(byte_arg)*8

	def _write_compressed(self, byte_arg: bytes) -> None:
		current_byte = len(byte_arg) - 1

		# Write upper bytes with a single 1
		# From high byte to low byte, if high byte is 0 then write 1. Otherwise write 0 and the remaining bytes
		while current_byte > 0:
			is_zero = byte_arg[current_byte] == 0
			self._write_bit(is_zero)
			if not is_zero:
				# Write the remainder of the data
				self._write_bytes(byte_arg[:current_byte + 1])
				return
			current_byte -= 1

		# If the upper half of the last byte is 0 then write 1 and the remaining 4 bits. Otherwise write 0 and the 8 bits.

		is_zero = byte_arg[0] & 0xF0 == 0x00
		self._write_bit(is_zero)
		if is_zero:
			self.write_bits(byte_arg[0], 4)
		else:
			self._write_bytes(byte_arg[:1])

	def align_write(self) -> None:
		"""Align the write offset to the byte boundary."""
		if self._write_offset % 8 != 0:
			self._alloc_bits(8 - self._write_offset % 8)
			self._write_offset += 8 - self._write_offset % 8

	def _alloc_bits(self, number_of_bits: int) -> None:
		bytes_to_allocate: int = math.ceil((self._write_offset + number_of_bits) / 8) - len(self._data)
		if bytes_to_allocate > 0:
			self._data += bytes(bytes_to_allocate)
