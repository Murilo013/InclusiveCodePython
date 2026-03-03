from fastapi import FastAPI
import subprocess
import tempfile
import shutil
import os
import stat
import time

app = FastAPI()

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

            return content[:1000]

    return None


def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


@app.post("/analyze")
def analyze(data: dict):
    repo_url = data["url"]

    try:
        repo_path = clone_repo(repo_url)

        time.sleep(1)

        readme_content = read_readme(repo_path)

        time.sleep(1)

        shutil.rmtree(repo_path, onerror=remove_readonly)

        if readme_content:
            return {
                "status": "success",
                "readme_preview": readme_content
            }
        else:
            return {
                "status": "success",
                "message": "README não encontrado"
            }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }