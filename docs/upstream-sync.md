# Upstream synchronization

`Sync BtbN upstream` checks `FFmpeg-Builds/FFmpeg-Builds:master` hourly, at
17 minutes past the hour. This is the canonical repository formerly hosted at
`BtbN/FFmpeg-Builds`. You can also use **Actions → Sync BtbN upstream → Run workflow**
on `master`. GitHub may delay scheduled jobs.
Changes to the sync workflow or script on `master` also run it, so deployment
checks the App configuration immediately.

The workflow fast-forwards `master-BtbN` to the exact upstream commit and opens or
updates one PR from `sync/btbn-master` into `master`. It never pushes to `master`
or merges the PR. The separate proposal branch allows conflict resolution without
changing the mirror. Neither branch is automatically force-pushed.

Before opening the PR, it inspects the three-way merge with the current `master`
without executing any upstream scripts. Ordinary Git merging preserves our local
changes. The check flags merge conflicts, removed release/image combinations,
missing release variant/addin files, and changes to the daily schedule, manual
release default or scheduled repository guard. These checks use our current
`master` as the baseline, so future intentional policy changes need no duplicated
configuration. The PR body lists the files changed by the proposed merge.

This is not a compilation or semantic compatibility check. Review dependency and
OS changes, including their interaction with our RISC-V fixes. Conflicts or failed
release checks produce a draft PR and a failed commit status. An already-open PR
gets its body and status updated; it is not automatically converted to draft or
marked ready. Checks are recalculated on each hourly/manual sync, not immediately
on every edit to `master`.

## One-time setup

1. Put these files on the default branch, `master`.
2. Register a private GitHub App with write access to **Contents**, **Workflows**,
   **Pull requests**, and **Commit statuses**. Disable webhooks; no callback URL
   or server is needed. Install it on `abrar71/FFmpeg-Builds` only. Workflow write
   access is necessary because upstream updates can modify `.github/workflows/`.
3. Under **Settings → Secrets and variables → Actions**, add the repository
   variable **`UPSTREAM_SYNC_APP_CLIENT_ID`** with the App's Client ID and the
   repository secret **`UPSTREAM_SYNC_APP_PRIVATE_KEY`** with the complete PEM
   private key, including its BEGIN/END lines. These are repository values;
   no GitHub Environment is needed. Do not use the App's OAuth client secret.
4. Check the first run after deployment, or run the workflow manually. If upstream is already
   included in `master`, no PR is needed.

The workflow uses `actions/create-github-app-token` to generate a temporary
installation token restricted to this repository and the four permissions above.
It uses that token for Git fetch/push, PRs, and commit statuses. The action revokes
the token at job completion; tokens otherwise expire after one hour. The old
`UPSTREAM_SYNC_TOKEN` repository secret is not used: that name now only identifies
the generated token in the script's environment.

The App creates PRs as its own bot identity, and its events can trigger PR checks
normally. Review the build results before merging. The **Allow GitHub Actions to
create and approve pull requests** setting is not required for this App flow.
The built-in `GITHUB_TOKEN` remains read-only for the sync job and has only
`actions: write` in the separate job that cancels mirror builds.

## Reviewing and resolving updates

Use **Create a merge commit** when accepting the PR. Squash/rebase merging loses
upstream ancestry and causes later syncs to propose previously reviewed commits
again. Close a PR to decline it: the same upstream revision is not automatically
reopened. A newer upstream commit can create another PR.

Resolve conflicts on `sync/btbn-master`, never on `master-BtbN`. The bot stops if
the proposal branch contains manual commits that are not in upstream, so it will
not overwrite your resolution. After merging such a PR, delete the proposal
branch (GitHub's **Delete branch** button) before the next sync. Rerun the workflow
after resolving a history issue. An upstream history rewrite also stops the bot
for manual review rather than silently force-pushing the mirror.

## CI on mirror branches

There is no standard per-branch switch in repository settings to disable Actions.
Because `master-BtbN` is an exact mirror, it retains upstream's push triggers.
The sync workflow listens for `Build FFmpeg` push runs on `master-BtbN` and
`sync/btbn-master` and immediately requests cancellation. It handles both new and
re-run jobs. It does not check out or execute code from the triggering branch.

GitHub can briefly queue or start build jobs before the cancellation listener is
scheduled; this is best-effort cancellation, not a guarantee of zero runner time.
Cancelled entries remain visible in Actions. Builds on `master` and PR validation
are unaffected. This listener depends on the upstream workflow name/path staying
`Build FFmpeg` / `.github/workflows/build.yml`; review changes to those identifiers.

## Local validation

With Python 3, Git 2.38+ and PyYAML 6.0.3 installed:

```bash
python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v
```

Tests use temporary local Git repositories and a fake API. They cover clean
merges, repeated syncs, PR updates, declined revisions, conflicting changes,
release safeguards, upstream rewrites and preservation of manual branch work.

GitHub references: [App token action](https://github.com/actions/create-github-app-token),
[workflow-trigger behavior](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow),
[workflow_run events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run),
and [Actions repository settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).
