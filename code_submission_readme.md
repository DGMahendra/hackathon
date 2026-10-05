# Hackathon Code Submission Repository

## Purpose

This repository is used to collect the final code submissions for the hackathon.

**Do NOT copy your project into this repository.**

Instead, you will push your **existing Git repository** into your assigned branch in this repository. This preserves your complete Git commit history, allowing judges to review:

- Complete development history
- Commit messages
- Commit timestamps
- Author information
- Development progress
- Merge history (if applicable)

---

# Submission Workflow

Your existing repository remains your primary repository.

This submission repository is added as a **secondary remote**.

```
Your Repository
│
├── origin
│   └── Your Team Repository
│
└── submission
    └── Hackathon Submission Repository
```

You will continue developing normally in your own repository and only push a copy of your work to the submission repository when required.

---

# Prerequisites

Before submitting:

- Your GitHub username has been shared with the repository administrator.
- You have been granted access to this private repository.
- Your project is already tracked using Git.

---

# Step 1 - Verify Repository Access

Open the submission repository in your browser.

If you cannot access it, contact the repository administrator before continuing.

---

# Step 2 - Open Your Existing Project

Navigate to your existing project.

Example:

```bash
cd my-awesome-project
```

Verify it is a Git repository:

```bash
git status
```

You should see something similar to:

```
On branch main
nothing to commit, working tree clean
```

---

# Step 3 - Verify Your Current Remote

Run:

```bash
git remote -v
```

Example:

```
origin  https://github.com/your-team/project.git (fetch)
origin  https://github.com/your-team/project.git (push)
```

This should be your team's repository.

---

# Step 4 - Add the Submission Repository

Add the submission repository as a second remote.

```bash
git remote add submission git@github.com:DataGrokrAnalytics/hackathon_2026.git
```

Verify:

```bash
git remote -v
```

Expected output:

```
origin      https://github.com/your-team/project.git (fetch)
origin      https://github.com/your-team/project.git (push)

submission  git@github.com:DataGrokrAnalytics/hackathon_2026.git (fetch)
submission  git@github.com:DataGrokrAnalytics/hackathon_2026.git (push)
```

---

# Step 5 - Push to Your Team Branch

Push your current branch to your assigned team branch.

```bash
git push submission HEAD:team/<team-name>
```

Example:

```bash
git push submission HEAD:team/team-alpha
```

Git will upload:

- Complete repository history
- All commits
- Branch history
- Latest project state

---

# If Your Default Branch is Different

If your default branch is called `main`:

```bash
git push submission main:team/<team-name>
```

If it is called `master`:

```bash
git push submission master:team/<team-name>
```

If it is called `develop`:

```bash
git push submission develop:team/<team-name>
```

Using `HEAD` is recommended because it automatically pushes whichever branch you currently have checked out.

---

# Step 6 - Verify Submission

Open the submission repository in GitHub.

Navigate to your assigned branch:

```
team/<team-name>
```

Verify:

- Your latest code is visible.
- Recent commits are present.
- Commit history has been preserved.

---

# Updating Your Submission

Continue working in your own repository as usual.

When you want to submit new changes:

```bash
git add .
git commit -m "Improve feature"

git push origin
git push submission HEAD:team/<team-name>
```

There is no need to add the submission remote again.

---

# Verify Your Remotes Anytime

```bash
git remote -v
```

Example:

```
origin      https://github.com/your-team/project.git
submission  git@github.com:DataGrokrAnalytics/hackathon_2026.git
```

---

# Branch Naming Convention

Each team has a dedicated branch.

Use only your assigned branch.

Example:

```
team/team-alpha
team/team-beta
team/team-gamma
```

Do **NOT** create additional branches.

---

# Repository Rules

## Allowed

- Push only to your assigned branch.
- Continue using your own repository as the primary development repository.
- Submit updates as often as needed.
- Preserve Git history.

## Not Allowed

- Push to `main`.
- Push to another team's branch.
- Force push unless instructed.
- Delete branches.
- Rewrite Git history after submission.

---

# Troubleshooting

## Permission denied

You have not yet been granted access.

Contact the repository administrator.

---

## Remote already exists

If a remote named `submission` already exists:

```bash
git remote remove submission

git remote add submission https://github.com/<organization>/<submission-repository>.git
```

---

## Repository not found

Verify:

- Repository URL
- GitHub access
- You are logged into the correct GitHub account

---

## Push rejected

Verify:

- You are pushing to the correct branch.
- You have permission to access the repository.
- Your branch name matches the assigned team branch.

---

# Frequently Asked Questions

### Will my Git history be preserved?

Yes.

The complete Git history is preserved, including all commits, authors, timestamps, and merge history.

---

### Does this change my existing repository?

No.

Your repository remains unchanged.

The submission repository is simply an additional remote.

---

### Can I continue working after submitting?

Yes.

Continue working normally in your own repository and push updates to the submission repository whenever required.

---

### Can I remove the submission remote later?

Yes.

```bash
git remote remove submission
```

This only removes the connection to the submission repository. Your project and Git history remain unchanged.