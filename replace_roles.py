import os
import glob

def replace_in_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    new_content = content.replace("UserRole.USER", "UserRole.CUSTOMER").replace("UserRole.RESELLER", "UserRole.AGENT")
    
    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

for root, dirs, files in os.walk("app"):
    for file in files:
        if file.endswith(".py"):
            replace_in_file(os.path.join(root, file))
