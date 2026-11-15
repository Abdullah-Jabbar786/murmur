# How We Work

Simple rules so three people can build one project without stepping on each other.

## One-Time Setup per Person

```bash
git clone https://github.com/Abdullah-Jabbar786/murmur.git
cd murmur
git config user.name "Your Name"
git config user.email "email-linked-to-your-github@example.com"
git config pull.rebase false          # git pull always merges, never rebases
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```

## The Daily Flow

```bash
git checkout main
git pull                               # get the latest
git checkout -b yourname/short-task    # e.g., hammad/s1-tracker
# ... do your work ...
make test                              # must be green locally
git status                             # check what you are about to add
git add <specific files or folders>    # NEVER "git add ."
git commit -m "feat: what you did"
git push -u origin yourname/short-task
```

Then on GitHub:
1. Click **Compare & pull request**.
2. Fill out the PR template and link the issue (`Closes #12`).
3. Label it (`transport`, `swarm`, `infra`, `dashboard`, `docs`, `bug`). Reviewers are requested automatically via `CODEOWNERS`.
4. Review within **24 hours**. Comments should be specific and constructive.
5. Once CI is green and approved: **Abdullah merges using Squash and merge**.
   - *Fallback rule:* If Abdullah is unreachable for 24 hours, the assigned reviewer may merge an approved PR with green CI.
6. The branch auto-deletes on GitHub. Locally:
```bash
git checkout main
git pull
git branch -d yourname/short-task
```

## If `main` moved while you were working
```bash
git fetch origin
git merge origin/main                  # fix conflicts, git add <file>, git commit, git push
```

## Rules

1. **Never commit directly to `main`**. Do your work on your feature branch.
2. **Never force push** (`git push --force` is prohibited).
3. **Never merge if CI is red**. `main` is always green. Broken `main` means revert first, debug second.
4. **Never `git add .`**. Always add specific files/folders and run `git status`.
5. **Contract changes** (`docs/` contracts) require sign-off from all three members.
6. **No large binary files in git**. Large pcaps and test files stay out of git (commit CSVs, plots, and small pcap samples only).
7. If you used AI for a task, note it in `docs/ai-disclosure.md` and check the box in the PR template.

## Review Rotation
Reviewers are defined in `.github/CODEOWNERS`:
- Abdullah reviews Hammad's work.
- Hammad reviews Majid's work.
- Majid reviews Abdullah's work.
Abdullah provides final review and merges.

## Commit Message Prefixes
Start with: `feat:` new feature, `fix:` bug fix, `docs:` documentation, `test:` tests, `exp:` experiments, `chore:` setup and maintenance.
