import os.path

from setuptools import find_packages, setup

with open(os.path.join(os.path.dirname(__file__), "README.md")) as file:
	long_desc = file.read()

setup(
	name="pyraknet",
	version="0.1.0",
	description="Minimal Python implementation of RakNet 3.25.",
	long_description=long_desc,
	author="lcdr",
	url="https://bitbucket.org/lcdr/pyraknet/",
	license="GPL v3",
	packages=find_packages(),
	python_requires=">=3.6",
)
