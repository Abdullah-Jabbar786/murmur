# How we work

Simple rules so three people can build one project without stepping on each other.

## The daily flow

```bash
git checkout main
git pull                          # get the latest
git checkout -b yourname/short-task   # new branch for your task
# ... do your work ...
git add .
git commit -m "feat: what you did"
git push -u origin yourname/short-task
```

Then on GitHub, click **Compare & pull request**, write one line about what you did, and ask a teammate to review it. They click **Merge** when it looks fine.

## Rules

1. Do your work on your own branch, not on `main`.
2. Every branch is merged by a pull request that another teammate looks at.
3. Pull before you start working each session.
4. Do not force push.
5. Do not merge if the CI check is red.
6. Use your GitHub-linked email so your commits count on your profile.

## Review rotation

Person A reviews B, B reviews C, C reviews A.

## Commit messages

Start with: `feat:` new thing, `fix:` bug fix, `docs:` documentation, `test:` tests, `exp:` experiments, `chore:` setup and cleanup.
