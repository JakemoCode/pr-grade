# Another process run between the check and the write is a second read.
import subprocess


def deploy(repo, name):
    current = repo.get(name)
    if current.locked:  # expect: check
        return
    subprocess.run(['make', 'deploy'], check=True)  # expect: gap second-read
    repo.update(name)  # expect: write
