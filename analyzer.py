import sys
import json
import os
import shutil
import tempfile
import subprocess
import stat
import time
import requests
from dotenv import load_dotenv

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


def analyze_accessibility_tags(web_files, api_key, api_url=None, max_chunk_chars=14000, timeout=30):
    """Analisa tags de acessibilidade em uma lista de arquivos web usando uma API de IA.

    Args:
        web_files (list): Lista de dicionários com `filename` e `content` (igual a saída de `read_web_files`).
        api_key (str): Chave de API para autenticação (Bearer).
        api_url (str, optional): Endpoint da API. Padrão é OpenAI chat completions endpoint.
        max_chunk_chars (int, optional): Tamanho máximo de caracteres por requisição.
        timeout (int, optional): Timeout em segundos para a requisição HTTP.

    Returns:
        dict: Resultado agregado com contagem de problemas e lista de issues por arquivo.
    """
    if api_url is None:
        api_url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Monta partes do prompt; pedimos JSON estrito de saída
    system_prompt = (
        "You are an accessibility auditor. Analyze the provided web files and return a JSON object. "
        "For each non-conforming accessibility tag, return filename, approximate line number, a short snippet, and the issue description. "
        "Also return the total number of non-conforming tags as `non_conforming_count` and a short `summary`. "
        "Respond with valid JSON only."
    )

    def make_user_prompt(chunk_files):
        parts = [
            "Analyze the following files for accessibility tag issues (missing/incorrect ARIA, missing alt, bad heading order, empty links, form controls without labels, etc.).",
            "Return JSON with: summary, non_conforming_count (int), issues (array of {filename, line, snippet, issue}).",
            "Files:",
        ]

        for f in chunk_files:
            parts.append(f"---FILE: {f['filename']}---\n{f['content']}")

        # ask model to prefer approximate line numbers if possible
        parts.append("If you can't determine an exact line number, provide an approximate location or null.")
        return "\n\n".join(parts)

    results = {
        "summary": "",
        "non_conforming_count": 0,
        "issues": [],
        "raw_ai_outputs": []
    }

    # Chunk files to stay under max_chunk_chars
    chunk = []
    chunk_len = 0

    def send_chunk(chunk_files):
        user_prompt = make_user_prompt(chunk_files)

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0
        }

        try:
            r = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()

            # Try to extract assistant content for OpenAI-like responses
            assistant_text = None
            if "choices" in data and len(data["choices"]) > 0:
                assistant_text = data["choices"][0].get("message", {}).get("content") or data["choices"][0].get("text")
            elif "output" in data:
                assistant_text = data.get("output")

            if not assistant_text:
                return None, f"No assistant text in response: {data}", None

            # Attempt to parse assistant_text as JSON
            try:
                parsed = json.loads(assistant_text)
                return parsed, None, assistant_text
            except Exception:
                # Try to extract JSON substring
                start = assistant_text.find("{")
                end = assistant_text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        parsed = json.loads(assistant_text[start:end+1])
                        return parsed, None, assistant_text
                    except Exception as e:
                        return None, f"Failed to parse JSON from assistant_text: {e}", assistant_text

                return None, "Assistant response did not contain valid JSON", assistant_text

        except Exception as e:
            return None, str(e), None

    for f in web_files:
        size = len(f.get("content", "")) + len(f.get("filename", ""))
        if chunk_len + size > max_chunk_chars and chunk:
            parsed, err, raw = send_chunk(chunk)
            if raw:
                results["raw_ai_outputs"].append(raw)
            if parsed and isinstance(parsed, dict):
                # aggregate
                if parsed.get("summary"):
                    results["summary"] += parsed.get("summary") + "\n"
                results["non_conforming_count"] += int(parsed.get("non_conforming_count", 0))
                for it in parsed.get("issues", []):
                    results["issues"].append(it)

            chunk = []
            chunk_len = 0

        chunk.append(f)
        chunk_len += size

    if chunk:
        parsed, err, raw = send_chunk(chunk)
        if raw:
            results["raw_ai_outputs"].append(raw)
        if parsed and isinstance(parsed, dict):
            if parsed.get("summary"):
                results["summary"] += parsed.get("summary") + "\n"
            results["non_conforming_count"] += int(parsed.get("non_conforming_count", 0))
            for it in parsed.get("issues", []):
                results["issues"].append(it)

    return results


def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


if __name__ == "__main__":
    repo_url = sys.argv[1]
    
    try:
        repo_path = clone_repo(repo_url)

        time.sleep(1)

        web_files = read_web_files(repo_path)

        # carregar .env e obter chave de API
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        # Se não houver variável OPENAI_API_URL, usar endpoint padrão da OpenAI
        api_url = os.getenv("OPENAI_API_URL") or "https://api.openai.com/v1/chat/completions"

        accessibility_report = None
        accessibility_error = None
        if api_key:
            try:
                accessibility_report = analyze_accessibility_tags(web_files, api_key, api_url)
            except Exception as e:
                accessibility_error = str(e)
        else:
            accessibility_error = "OPENAI_API_KEY not set; skipping accessibility analysis."

        time.sleep(1)

        shutil.rmtree(repo_path, onerror=remove_readonly)
        
        # Retornar apenas o feedback da análise (sem incluir o conteúdo dos arquivos)
        if accessibility_report is not None:
            out = {
                "status": "success",
                "accessibility_report": accessibility_report
            }
            print(json.dumps(out))
        else:
            # Se não houve relatório (por exemplo falta de chave), devolve erro/aviso
            out = {
                "status": "success" if accessibility_error is None else "warning",
                "accessibility_report": accessibility_report,
                "accessibility_error": accessibility_error
            }
            print(json.dumps(out))
        
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "message": str(e)
        }))