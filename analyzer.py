import sys
import json
import os
import shutil
import tempfile
import subprocess
import stat
import time

def clone_repo(repo_url):
    temp_dir = tempfile.mkdtemp()
    
    subprocess.run(
        ["git", "clone", repo_url, temp_dir],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )
    
    return temp_dir


def read_readme(repo_path):
    for file in os.listdir(repo_path):
        if file.lower().startswith("readme"):
            readme_path = os.path.join(repo_path, file)
            
            with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            return content[:1000]  # retorna apenas primeiros 1000 caracteres
    
    return None


def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


if __name__ == "__main__":
    repo_url = sys.argv[1]
    
    try:
        repo_path = clone_repo(repo_url)

        time.sleep(1)

        readme_content = read_readme(repo_path)

        time.sleep(1)

        shutil.rmtree(repo_path, onerror=remove_readonly)
        
        if readme_content:
            print(json.dumps({
                "status": "success",
                "readme_preview": readme_content
            }))
        else:
            print(json.dumps({
                "status": "success",
                "message": "README não encontrado"
            }))
        
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "message": str(e)
        }))