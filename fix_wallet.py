import re

with open("app/api/v1/endpoints/wallet.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_monnify_block = False
monnify_start = -1
monnify_end = -1

for i, line in enumerate(lines):
    if line.strip() == 'if is_gateway_active(db, "monnify", True):':
        monnify_start = i
        break

for i in range(monnify_start + 1, len(lines)):
    if lines[i].strip() == '# 3. Recreate / Update Billstack Reserved Account':
        monnify_end = i
        break

if monnify_start != -1 and monnify_end != -1:
    for i in range(monnify_start + 1, monnify_end):
        if lines[i].strip():
            lines[i] = "    " + lines[i]

with open("app/api/v1/endpoints/wallet.py", "w") as f:
    f.writelines(lines)
