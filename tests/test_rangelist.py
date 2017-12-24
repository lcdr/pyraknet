import random
import unittest

from pyraknet.rangelist import RangeList, ReadStream

class RangeListTest(unittest.TestCase):
	def setUp(self):
		self.list = RangeList()

	def tearDown(self):
		# for each testcase:
		# test that __len__ is equal to number of items returned by __iter__
		self.assertEqual(len(list(self.list)), len(self.list))
		# test that all items returned by iter are contained
		for item in self.list:
			self.assertTrue(item in self.list)
		# test that all items returned by holes are not contained
		for item in self.list.holes():
			self.assertFalse(item in self.list)
		# check that the serialization / deserialization match
		stream = ReadStream(bytes(self.list.serialize()))
		new_list = RangeList(stream)
		self.assertEqual(list(self.list), list(new_list))
		# also check that the list is empty after clear
		self.list.clear()
		self.test_bool_false()
		self.assertEqual(list(self.list), [])

	def test_bool_false(self):
		self.assertFalse(self.list)

	def test_bool_true(self):
		self.list.insert(1)
		self.assertTrue(self.list)

	def test_insert_duplicate(self):
		values = [1, 1, 2, 2, 3, 3]
		for value in values:
			self.list.insert(value)
		self.assertEqual(list(self.list), sorted(list(set(values))))
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert_within(self):
		values = [1, 5, 2, 4, 3]
		for value in values:
			self.list.insert(value)
		self.assertEqual(list(self.list), sorted(list(set(values))))
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert(self):
		values = [1, 2, 3, 4]
		for value in values:
			self.list.insert(value)
		self.assertEqual(list(self.list), values)
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert_reversed(self):
		original = [1, 2, 3, 4]
		values = reversed(original)
		for value in values:
			self.list.insert(value)
		self.assertEqual(list(self.list), original)
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert_first_last_middle(self):
		self.list.insert(1)
		self.assertEqual(list(self.list), [1])
		self.assertEqual(len(self.list._ranges), 1)
		self.list.insert(3)
		self.assertEqual(list(self.list), [1, 3])
		self.assertEqual(len(self.list._ranges), 2)
		self.list.insert(2)
		self.assertEqual(list(self.list), [1, 2, 3])
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert_last_first_middle(self):
		self.list.insert(3)
		self.assertEqual(list(self.list), [3])
		self.assertEqual(len(self.list._ranges), 1)
		self.list.insert(1)
		self.assertEqual(list(self.list), [1, 3])
		self.assertEqual(len(self.list._ranges), 2)
		self.list.insert(2)
		self.assertEqual(list(self.list), [1, 2, 3])
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert_outlier(self):
		self.list.insert(1)
		self.assertEqual(list(self.list), [1])
		self.assertEqual(len(self.list._ranges), 1)
		self.list.insert(3)
		self.assertEqual(list(self.list), [1, 3])
		self.assertEqual(len(self.list._ranges), 2)
		self.list.insert(2)
		self.assertEqual(list(self.list), [1, 2, 3])
		self.assertEqual(len(self.list._ranges), 1)
		self.list.insert(20)
		self.assertEqual(list(self.list), [1, 2, 3, 20])
		self.assertEqual(len(self.list._ranges), 2)

	def test_insert_extend(self):
		self.list.insert(5)
		self.assertEqual(list(self.list), [5])
		self.assertEqual(len(self.list._ranges), 1)
		self.list.insert(4)
		self.assertEqual(list(self.list), [4, 5])
		self.assertEqual(len(self.list._ranges), 1)
		self.list.insert(6)
		self.assertEqual(list(self.list), [4, 5, 6])
		self.assertEqual(len(self.list._ranges), 1)

	def test_insert_random(self):
		for _ in range(100):
			self.list.clear()
			values = [random.randrange(100) for _ in range(100)]
			for value in values:
				self.list.insert(value)
			self.assertEqual(list(self.list), sorted(set(values)))

	def test_deserialize_insert(self):
		values = [1, 2, 4, 5]
		for value in values:
			self.list.insert(value)
		stream = ReadStream(bytes(self.list.serialize()))
		new_list = RangeList(stream)
		new_list.insert(3)
		self.assertEqual(list(new_list), [1, 2, 3, 4, 5])

	def test_num_holes(self):
		values = [1, 2, 4, 5, 8, 9, 15, 19]
		for value in values:
			self.list.insert(value)
		self.assertEqual(self.list.num_holes(), 11)

	def test_holes(self):
		values = [1, 2, 4, 5, 8, 9, 15, 19]
		for value in values:
			self.list.insert(value)
		self.assertEqual(list(self.list.holes()), [3, 6, 7, 10, 11, 12, 13, 14, 16, 17, 18])

	def test_holes_num_holes(self):
		values = [1, 2, 4, 5, 8, 9, 15, 19]
		for value in values:
			self.list.insert(value)
		self.assertEqual(len(list(self.list.holes())), self.list.num_holes())
