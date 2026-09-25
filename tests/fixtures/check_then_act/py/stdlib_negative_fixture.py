# Standard library calls and builtins touch no shared state, and the read a check tests is not its own gap.
import argparse
import subprocess
import sys


def main(argv):
    args = argparse.ArgumentParser().parse_args(argv)
    run = subprocess.run(['git', 'status'], capture_output=True, text=True)
    if run.returncode != 0:
        sys.stdout.write(run.stderr)
        return 1
    seen = set(run.stdout.split())
    if args.publish:
        publish(seen)
    return 0


def publish(seen):
    pass
