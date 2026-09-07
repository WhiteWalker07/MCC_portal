# Deploying the lab PC

This OS decision has gone back and forth this session, so both paths are kept
here rather than assuming it's settled for good:

- **[`README-windows.md`](README-windows.md) + [`setup.ps1`](setup.ps1)** —
  Windows Server, the current target.
- **[`README-linux.md`](README-linux.md) + [`setup.sh`](setup.sh)** —
  Ubuntu Server 24.04 LTS, kept in case the target machine changes again.

One step applies no matter which OS wins:

## Make CI actually gate the auto-merge (one-time GitHub setting)

Every push to `develop` opens (or updates) a PR into `main` and turns on
GitHub's native auto-merge for it (`.github/workflows/auto-pr.yml`) — it only
merges once the test-suite workflow (`.github/workflows/ci.yml`) goes green,
same as a human reviewer refusing to approve a broken change. That gate is
**not enforced** until `main` has a branch-protection rule requiring it —
without one, GitHub has nothing to make auto-merge wait on.

1. **[github.com/WhiteWalker07/MCC_portal/settings/branches](https://github.com/WhiteWalker07/MCC_portal/settings/branches)**
2. Add a branch protection rule for `main`
3. Enable **"Require status checks to pass before merging"**, then search for
   and select **`CI / test`** (the job in `ci.yml`)
4. Save

Until this is set, pushes still open/update the PR and *attempt* to enable
auto-merge, but that attempt fails harmlessly (logged as a workflow warning,
not a failure) — the PR just sits open for manual merging instead, same as
before this automation existed.
