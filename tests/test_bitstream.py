import unittest

from pyraknet.bitstream import BitStream

class BitStreamTest(unittest.TestCase):
	def setUp(self):
		self.stream = BitStream()

class GeneralTest(BitStreamTest):
	def test_read_bytes_too_much(self):
		with self.assertRaises(EOFError):
			self.stream.read(bytes, length=2)

	def test_read_bytes_too_much_shifted(self):
		self.stream.write_bits(0xff, 1)
		self.stream.read_bits(1)
		with self.assertRaises(EOFError):
			self.stream.read(bytes, length=1)

class StringTest:
	STRING = None
	@classmethod
	def setUpClass(cls):
		if isinstance(cls.STRING, str):
			cls.CHAR_SIZE = 2
		else:
			cls.CHAR_SIZE = 1

	def test_allocated_bad_length(self):
		for func, param in ((self.stream.write, self.STRING), (self.stream.read, type(self.STRING))):
			with self.subTest():
				with self.assertRaises(TypeError):
					func(param, allocated_length=22.5)
			with self.subTest():
				with self.assertRaises(ValueError):
					func(param, allocated_length=-5)

	def test_write_allocated(self):
		self.stream.write(self.STRING, allocated_length=len(self.STRING)+10)
		if isinstance(self.STRING, str):
			encoded = self.STRING.encode("utf-16-le")
		else:
			encoded = self.STRING
		self.assertEqual(self.stream, encoded+bytes((len(self.STRING)+10)*self.CHAR_SIZE-len(encoded)))

	def test_write_allocated_long(self):
		with self.assertRaises(ValueError):
			self.stream.write(self.STRING, allocated_length=len(self.STRING)-2)

	def test_read_allocated(self):
		self.test_write_allocated()
		value = self.stream.read(type(self.STRING), allocated_length=len(self.STRING)+10)
		self.assertEqual(value, self.STRING)

	def test_read_allocated_buffergarbage(self):
		self.stream.write(self.STRING, allocated_length=len(self.STRING)+1)
		self.stream.write(b"\xdf"*10*self.CHAR_SIZE)
		value = self.stream.read(type(self.STRING), allocated_length=len(self.STRING)+1+10)
		self.assertEqual(value, self.STRING)

	def test_read_allocated_no_terminator(self):
		self.stream.write(b"\xff"*33*self.CHAR_SIZE)
		with self.assertRaises(RuntimeError):
			self.stream.read(type(self.STRING), allocated_length=33)

class UnicodeStringTest(StringTest, BitStreamTest):
	STRING = "Hello world"

class ByteStringTest(StringTest, BitStreamTest):
	STRING = UnicodeStringTest.STRING.encode("latin1")
