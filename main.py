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


def read_web_files(repo_path):
    web_files = []
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            if file.lower().endswith(('.html', '.jsx', '.tsx')):
                file_path = os.path.join(root, file)
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    
                    # Remove o caminho base do repositório do nome do arquivo
                    relative_path = os.path.relpath(file_path, repo_path)
                    
                    web_files.append({
                        "filename": relative_path,
                        "content": content
                    })
                except Exception as e:
                    continue
    
    return web_files


def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


@app.post("/analyze")
def analyze(data: dict):
    repo_url = data["url"]

    try:
        repo_path = clone_repo(repo_url)

        time.sleep(1)

        web_files = read_web_files(repo_path)

        time.sleep(1)

        shutil.rmtree(repo_path, onerror=remove_readonly)

        if web_files:
            return {
                "status": "success",
                "web_files": web_files
            }
        else:
            return {
                "status": "success",
                "message": "Nenhum arquivo web (.html, .jsx, .tsx) encontrado"
            }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }