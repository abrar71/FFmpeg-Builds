#!/usr/bin/env python3
"""Mirror upstream and propose a normal merge; never write master."""

import base64
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml


UPSTREAM = "https://github.com/FFmpeg-Builds/FFmpeg-Builds.git"
MIRROR = "master-BtbN"
PROPOSAL = "sync/btbn-master"
WORKFLOW = ".github/workflows/build.yml"
CONTEXT = "Upstream sync / merge and release settings"


class Git:
    def __init__(self, path=".", token=None):
        self.path = path
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if token:
            value = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            self.env.update(GIT_CONFIG_COUNT="1",
                            GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                            GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {value}")

    def run(self, *args, check=True):
        result = subprocess.run(["git", *args], cwd=self.path, env=self.env,
                                text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result

    def text(self, *args):
        return self.run(*args).stdout.strip()

    def ancestor(self, old, new):
        result = self.run("merge-base", "--is-ancestor", old, new, check=False)
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr)
        return result.returncode == 0

    def ref(self, name):
        result = self.run("rev-parse", "--verify", name, check=False)
        return result.stdout.strip() if result.returncode == 0 else None


class GitHub:
    def __init__(self, repository, token):
        self.repository = repository
        self.token = token

    def request(self, method, path, data=None):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/{path}",
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Never log request headers or credentials.
            raise RuntimeError(f"GitHub {method} {path}: HTTP {error.code}; "
                               "check App installation access and token permissions") from None

    def pulls(self):
        query = urllib.parse.urlencode({"state": "all", "base": "master",
                                        "head": self.repository.split('/')[0] + ':' + PROPOSAL,
                                        "sort": "created", "direction": "desc",
                                        "per_page": 100})
        return self.request("GET", f"pulls?{query}")


def matrix_rows(matrix):
    axes = {key: value for key, value in matrix.items() if key not in ("include", "exclude")}
    if any(not isinstance(value, list) for value in axes.values()):
        raise ValueError("Dynamic matrices need manual review")
    original = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    original = [row for row in original if not any(
        all(row.get(key) == value for key, value in exclusion.items())
        for exclusion in matrix.get("exclude", []))]
    rows = [dict(row) for row in original]
    for extra in matrix.get("include", []):
        matches = [i for i, row in enumerate(original)
                   if all(key not in row or row[key] == value for key, value in extra.items())]
        if matches:
            for index in matches:
                rows[index].update(extra)
        else:
            rows.append(extra)
    return rows


def release_settings(text):
    # BaseLoader keeps the YAML key 'on' a string instead of a YAML 1.1 boolean.
    data = yaml.load(text, Loader=yaml.BaseLoader)
    events = data["on"]
    jobs = data["jobs"]
    release = matrix_rows(jobs["build_ffmpeg"]["strategy"]["matrix"])
    images = matrix_rows(jobs["build_targets"]["strategy"]["matrix"])
    guards = [match for step in jobs["pre_check"]["steps"]
              for match in re.findall(r'\$GITHUB_REPOSITORY"\s*!=\s*"([^"]+)"', step.get("run", ""))]
    if not guards:
        raise ValueError("Could not identify the scheduled repository guard")
    return {
        "daily schedule": events["schedule"],
        "manual release default": events["workflow_dispatch"]["inputs"]["doRelease"]["default"],
        "scheduled repository guard": guards,
        "release combinations": {(row["target"], row["variant"]) for row in release},
        "dependency image combinations": {(row["target"], row["variant"], row.get("arch", "x86"))
                                           for row in images},
    }


def inspect_merge(git, base, upstream):
    result = git.run("merge-tree", "--write-tree", base, upstream, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Cannot inspect merge: {result.stderr}")
    if result.returncode == 1:
        conflicts = sorted(set(re.findall(r'^CONFLICT.*$', result.stdout, re.MULTILINE)))
        return ["Merge conflicts require manual resolution."], conflicts
    tree = result.stdout.splitlines()[0]
    problems = []
    try:
        before = release_settings(git.text("show", f"{base}:{WORKFLOW}"))
        after = release_settings(git.text("show", f"{tree}:{WORKFLOW}"))
        for key, expected in before.items():
            if isinstance(expected, set):
                missing = expected - after[key]
                if missing:
                    problems.append(f"Removed {key}: {sorted(missing)}")
            elif expected != after[key]:
                problems.append(f"Changed {key}: {expected!r} → {after[key]!r}")
        for target, variant in before["release combinations"]:
            license_name, *addins = variant.split()
            for path in [f"variants/{target}-{license_name}.sh", *[f"addins/{a}.sh" for a in addins]]:
                if git.run("cat-file", "-e", f"{tree}:{path}", check=False).returncode:
                    problems.append(f"Missing release input: {path}")
    except (KeyError, TypeError, ValueError, yaml.YAMLError, RuntimeError) as error:
        problems.append(f"Release settings could not be verified: {error}")
    changed = git.text("diff", "--name-only", base, tree).splitlines()
    return sorted(set(problems)), changed


def sync(git, api, origin, upstream=UPSTREAM):
    # Fetch into private refs; the checked-out files and local branches stay untouched.
    git.run("fetch", "--no-tags", "--prune", origin,
            "+refs/heads/*:refs/sync/origin/*")
    git.run("fetch", "--no-tags", upstream, "+refs/heads/master:refs/sync/upstream")
    base = git.text("rev-parse", "refs/sync/origin/master")
    tip = git.text("rev-parse", "refs/sync/upstream")
    for branch in (MIRROR, PROPOSAL):
        previous = git.ref(f"refs/sync/origin/{branch}")
        if previous and not git.ancestor(previous, tip):
            raise RuntimeError(f"{branch} diverged from upstream. Nothing was pushed. "
                               "Review upstream history or manual branch changes; no automatic force-push.")

    if git.ancestor(tip, base):
        git.run("push", origin, f"{tip}:refs/heads/{MIRROR}")
        return "Upstream is already included in master; mirror checked."

    pulls = api.pulls()
    opened = next((pr for pr in pulls if pr["state"] == "open"), None)
    if not opened and pulls and pulls[0]["head"]["sha"] == tip and not pulls[0].get("merged_at"):
        git.run("push", origin, f"{tip}:refs/heads/{MIRROR}")
        return "This upstream revision was declined; waiting for a newer upstream commit."

    problems, details = inspect_merge(git, base, tip)
    # Both branches fast-forward together, or neither moves. Master is never a push target.
    git.run("push", "--atomic", origin, f"{tip}:refs/heads/{MIRROR}", f"{tip}:refs/heads/{PROPOSAL}")
    merge_base = git.text("merge-base", base, tip)
    count = git.text("rev-list", "--count", f"{base}..{tip}")
    status = "Manual review required" if problems else "Merge is clean; existing release settings are retained"
    body = ("<!-- btbn-upstream-sync -->\n"
            f"Bring {count} upstream commits into `master` while retaining this fork's patches.\n\n"
            f"[Upstream changes](https://github.com/FFmpeg-Builds/FFmpeg-Builds/compare/{merge_base}...{tip})\n\n"
            f"Checked against our master `{base[:12]}` and upstream `{tip[:12]}`.\n\n"
            f"**{status}.**\n\n")
    if problems:
        body += "\n".join(f"- {problem}" for problem in problems) + "\n\n"
    body += ("The check covers the schedule, release default, repository guard, existing release/image "
             "combinations and their input files. It does not compile FFmpeg or prove that dependency/OS "
             "changes preserve the RISC-V fixes. Review those changes and the build results.\n\n"
             "Use **Create a merge commit**, not squash or rebase, to retain upstream ancestry. "
             "Approve the PR build workflows when GitHub requests it. No automatic merge is enabled.\n\n"
             "<details><summary>Changed files or merge conflicts</summary>\n\n```text\n"
             + "\n".join(details)[:12000] + "\n```\n</details>\n")
    data = {"title": f"Merge BtbN upstream master ({tip[:12]})", "body": body}
    if opened:
        pr = api.request("PATCH", f"pulls/{opened['number']}", data)
    else:
        pr = api.request("POST", "pulls", dict(data, head=PROPOSAL, base="master", draft=bool(problems)))
    api.request("POST", f"statuses/{tip}", {
        "state": "failure" if problems else "success", "context": CONTEXT,
        "description": "Conflicts or release settings need review" if problems else "Clean merge; existing release settings retained",
        "target_url": pr["html_url"],
    })
    if problems:
        raise RuntimeError(f"{pr['html_url']} needs manual resolution: " + "; ".join(problems))
    return f"PR ready for review: {pr['html_url']}"


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ.get("UPSTREAM_SYNC_TOKEN")
    if not token:
        raise RuntimeError("UPSTREAM_SYNC_TOKEN must contain the generated GitHub App installation token. "
                           "Check the App configuration in docs/upstream-sync.md.")
    result = sync(Git(token=token), GitHub(repository, os.environ["GH_TOKEN"]),
                  f"https://github.com/{repository}.git")
    print(result)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write(result + "\n")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
