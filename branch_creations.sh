#!/bin/bash

branches=(
"night-owls"
"team-kb"
"hooksmith"
"tag-titans"
"rogue-agents"
"blank"
"agentforge"
"airlock"
"graphgenie"
"opsmind"
"finops-forge"
)

git checkout main

for branch in "${branches[@]}"; do
    git checkout -b "team/$branch"
    git push -u submission "team/$branch"
    git checkout main
done

# for branch in "${branches[@]}"; do
#     echo "Deleting team/$branch..."

#     # Delete local branch (ignore if it doesn't exist)
#     # git branch -D "team/$branch" 2>/dev/null || true

#     # Delete remote branch
#     git push submission --delete "team/$branch"
# done

# echo "Done."