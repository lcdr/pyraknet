import os.path

from setuptools import setup

root_path = os.path.abspath(os.path.join(__file__, ".."))

setup(
	name="pyraknet",
	version="0.1dev",
	description="Minimal Python implementation of RakNet 3.25.",
	long_description=open(os.path.join(root_path, "README.md")).read(),
	author="lcdr",
	url="https://bitbucket.org/lcdr/pyraknet/",
	license="GPL v3",
	packages=["pyraknet"],
	package_dir={"pyraknet": root_path}
)