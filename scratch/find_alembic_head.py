import os
import re

dir_path = "/Users/mustaphamelemustapha/Code/VTU/vtu-backend/alembic/versions"
revisions = {}
down_revisions = {}

for f in os.listdir(dir_path):
    if not f.endswith(".py"):
        continue
    filepath = os.path.join(dir_path, f)
    with open(filepath, "r") as file:
        content = file.read()
        rev_match = re.search(r"revision\s*:\s*str\s*=\s*['\"]([^'\"]+)['\"]", content)
        down_match = re.search(r"down_revision\s*(?::\s*Union\[str,\s*None\])?\s*=\s*['\"]([^'\"]+)['\"]", content)
        if rev_match:
            rev = rev_match.group(1)
            down = down_match.group(1) if down_match else None
            revisions[rev] = f
            if down:
                down_revisions[down] = rev

# The head is a revision that is not in down_revisions keys (nobody revises it)
all_revs = set(revisions.keys())
all_downs = set(down_revisions.keys())
heads = all_revs - all_downs
print("Heads:", heads)
print("Revisions map:", {r: revisions[r] for r in heads})
