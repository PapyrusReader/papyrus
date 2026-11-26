"""Calculate a Papyrus release number from the authoritative workspace manifest."""
import argparse
import json
import re
from pathlib import Path


def parts(version):
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Expected MAJOR.MINOR.PATCH")
    return tuple(map(int, version.split(".")))


def next_number(manifest, bump, explicit=None):
    previous = parts(manifest["version"])
    previous_build = manifest["android_build_number"]
    if explicit:
        version, _, number = explicit.partition("+")
        if parts(version) <= previous or not re.fullmatch(r"[1-9][0-9]*", number):
            raise ValueError("Provide an increasing version and Android build number")
        build = int(number)
    else:
        index = ("major", "minor", "patch").index(bump)
        updated = [*previous[:index], previous[index] + 1, *([0] * (2 - index))]
        version = ".".join(map(str, updated))
        build = previous_build + 1
    if not previous_build < build <= 2100000000:
        raise ValueError("Android build number must increase and stay within the Play limit")
    return f"{version}+{build}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bump", choices=["major", "minor", "patch"])
    parser.add_argument("--number")
    args = parser.parse_args()
    print(next_number(json.loads(Path("release.json").read_text()), args.bump, args.number))
