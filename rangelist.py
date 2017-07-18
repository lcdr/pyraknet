from collections.abc import Iterable, Sized

from .bitstream import BitStream, c_bit, c_uint, c_ushort

class RangeList(Iterable, Sized):
	"""
	List that stores integers and compresses them to ranges if possible.
	To add an item, use insert.
	To get the uncompressed ranges, use ranges.

	Internal:
		The internal list of ranges is auto-sorted.
		Ranges in the internal representation are inclusive from both ends (that is, (20, 25) contains both 20 and 25 and everything in between)
	"""

	def __init__(self, input_stream=None):
		"""Init the rangelist, optionally by deserializing from a bitstream."""
		self._ranges = []
		if input_stream is not None:
			count = input_stream.read(c_ushort, compressed=True)
			for _ in range(count):
				max_equal_to_min = input_stream.read(c_bit)
				min = input_stream.read(c_uint)
				if max_equal_to_min:
					max = min
				else:
					max = input_stream.read(c_uint)
				self._ranges.append([min, max])

	def __bool__(self):
		return bool(self._ranges)

	def __len__(self):
		len = 0
		for min, max in self._ranges:
			len += max - min + 1
		return len

	def __iter__(self):
		"""Yield the numbers in the ranges, basically uncompressing the ranges."""
		for min, max in self._ranges:
			yield from range(min, max + 1)

	def clear(self):
		self._ranges.clear()

	def holes(self):
		"""Yield the items 'between' the ranges."""
		last_max = None
		for min, max in self._ranges:
			if last_max is not None:
				yield from range(last_max + 1, min)
			last_max = max

	def num_holes(self):
		"""Return the number of items 'between' the ranges."""
		num_holes = 0
		last_max = None
		for min, max in self._ranges:
			if last_max is not None:
				num_holes += min - last_max - 1
			last_max = max
		return num_holes

	def insert(self, item):
		iter_ = iter(self._ranges)
		for range in iter_:
			if range[0] == item + 1: # The item can extend the range
				range[0] -= 1
				break
			if range[0] <= item:
				if range[1] == item - 1: # The item can extend the range
					range[1] += 1
					try:
						nextrange = next(iter_)
						if nextrange[0] == item + 1: # The newly updated list has a max of one less than the next list, in which case we can merge these
							# Merge the ranges
							range[1] = nextrange[1]
							self._ranges.remove(nextrange)
					except StopIteration:
						pass
					break
				if range[1] >= item: # The item is within the range, we don't even need to update it
					break
				continue # The item is higher than the current range, check next range
			# If we got here, the range starts at a higher position than the item, so we should insert it now (the list is auto-sorted so there can't be any other position)
			self._ranges.insert(self._ranges.index(range), [item, item])
			break
		else:
			# We ran through the whole list and couldn't find a good existing range
			self._ranges.append([item, item])

	def serialize(self):
		"""
		Serialize the RangeList. This is meant to be compatible with RakNet's serialization.
		(This currently serializes items as uints, since currently the only occurrence where I need to serialize a rangelist is with an uint)
		"""
		out = BitStream()
		out.write(c_ushort(len(self._ranges)), compressed=True)
		for min, max in self._ranges:
			out.write(c_bit(min == max))
			out.write(c_uint(min))
			if min != max:
				out.write(c_uint(max))
		return out
