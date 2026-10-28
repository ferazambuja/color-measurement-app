"""Hatchling builds with archive-specific PEP 639 licence metadata.

The sdist includes the GPL contribution; wheels contain the AGPL Python
application plus MIT Three.js and CC BY-SA CIE original/derived assets.
Resolve the dynamic licence before constructing either builder,
including when rebuilding a wheel from an sdist's cached PKG-INFO.
"""

import os
import tomllib

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.builders.sdist import SdistBuilder
from hatchling.builders.wheel import WheelBuilder


def _builder(source=False):
    with open("pyproject.toml", "rb") as stream:
        config = tomllib.load(stream)
    config["project"]["license"] = "AGPL-3.0-only AND MIT AND CC-BY-SA-4.0"
    if source:
        config["project"]["license"] += " AND GPL-2.0-or-later"
    config["project"]["dynamic"].remove("license")
    builder = SdistBuilder if source else WheelBuilder
    return builder(os.getcwd(), config=config)


def build_sdist(sdist_directory, config_settings=None):
    return os.path.basename(next(_builder(True).build(
        directory=sdist_directory, versions=["standard"])))


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return os.path.basename(next(_builder().build(
        directory=wheel_directory, versions=["standard"])))


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return os.path.basename(next(_builder().build(
        directory=wheel_directory, versions=["editable"])))


def get_requires_for_build_sdist(config_settings=None):
    return _builder(True).config.dependencies


def get_requires_for_build_wheel(config_settings=None):
    return _builder().config.dependencies


def get_requires_for_build_editable(config_settings=None):
    from hatchling.builders.constants import EDITABLES_REQUIREMENT

    return [*_builder().config.dependencies, EDITABLES_REQUIREMENT]


class SourceMetadata(BuildHookInterface):
    def initialize(self, version, build_data):
        # Unlike most resolved fields, this one can differ in the wheel. Record
        # that explicitly in PKG-INFO for consumers following PEP 643.
        if "license" not in self.metadata.core.dynamic:
            self.metadata.core.dynamic.append("license")
