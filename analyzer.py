import sys
import json
import os
import shutil
import tempfile
import subprocess
import stat
import time
import re
import google.generativeai as genai
from dotenv import load_dotenv

MAX_FILE_SIZE = 8000


def clone_repo(repo_url):
    temp_dir = tempfile.mkdtemp()

    print("Clonando repositório...")

    subprocess.run(
        ["git", "clone", repo_url, temp_dir],
        check=True
    )

    return temp_dir


def read_web_files(repo_path):
    web_files = []

    for root, dirs, files in os.walk(repo_path):
        for file in files:

            if file.lower().endswith((".html", ".jsx", ".tsx")):
                file_path = os.path.join(root, file)

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()[:MAX_FILE_SIZE]

                    relative_path = os.path.relpath(file_path, repo_path)

                    web_files.append({
                        "filename": relative_path,
                        "content": content
                    })

                except Exception:
                    print("Erro lendo arquivo:", file_path)

    return web_files


def extract_json_from_text(text):
    """
    Tenta extrair JSON de uma resposta que pode conter texto extra
    ou markdown ```json
    """

    # remove blocos markdown
    text = text.replace("```json", "").replace("```", "")

    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            json_text = match.group(0)
            return json.loads(json_text)
    except Exception:
        return None

    return None


def analyze_accessibility_tags(web_files, confirm_mode=False):

    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise Exception("GEMINI_API_KEY não encontrada no .env")

    genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-2.5-flash")

    system_prompt = """
Você é um auditor de acessibilidade.

Analise os arquivos web fornecidos e detecte problemas como:

- imagens sem atributo alt
- links vazios
- botões sem nome acessível
- hierarquia incorreta de headings
- inputs sem label
- iframe sem título

Retorne SOMENTE JSON válido neste formato:

{
  "summary": "string",
  "non_conforming_count": number,
  "issues": [
    {
      "filename": "string",
      "line": number | null,
      "snippet": "string",
      "issue": "string"
    }
  ]
}
"""

    prompt_parts = [system_prompt, "\nFiles:\n"]

    for f in web_files:
        prompt_parts.append(
            f"\n---FILE: {f['filename']}---\n{f['content']}"
        )

    prompt = "\n".join(prompt_parts)

    print("\n====== PROMPT ENVIADO ======\n")
    print(prompt[:3000])
    print("\n============================\n")

    if confirm_mode:
        ans = input("Enviar prompt para IA? [y/N]: ")
        if ans.lower() != "y":
            print("Cancelado pelo usuário")
            return None

    print("Enviando requisição para Gemini...")

    response = model.generate_content(
        prompt,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "non_conforming_count": {"type": "number"},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "line": {"type": "number"},
                                "snippet": {"type": "string"},
                                "issue": {"type": "string"}
                            },
                            "required": ["filename", "snippet", "issue"]
                        }
                    }
                },
                "required": ["summary", "non_conforming_count", "issues"]
            }
        }
    )

    text = response.text

    print("\n====== RESPOSTA GEMINI ======\n")
    print(text[:2000])
    print("\n==============================\n")

    # tenta converter diretamente
    try:
        parsed = json.loads(text)
        return parsed

    except json.JSONDecodeError:
        print("JSON direto falhou, tentando extrair JSON...")

    # fallback
    parsed = extract_json_from_text(text)

    if parsed:
        return parsed

    print("Resposta da IA não é JSON válido")

    return {
        "summary": "Gemini did not return valid JSON",
        "non_conforming_count": 0,
        "issues": []
    }


def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(
        description="Accessibility Analyzer using Gemini AI"
    )

    parser.add_argument(
        "repo_url",
        help="Repository URL"
    )

    parser.add_argument(
        "--confirm",
        "-c",
        action="store_true",
        help="Confirm before sending prompt to AI"
    )

    args = parser.parse_args()

    repo_url = args.repo_url
    confirm_mode = args.confirm

    try:

        repo_path = clone_repo(repo_url)

        time.sleep(1)

        web_files = read_web_files(repo_path)

        print("Arquivos encontrados:", len(web_files))

        accessibility_report = analyze_accessibility_tags(
            web_files,
            confirm_mode
        )

        time.sleep(1)

        shutil.rmtree(repo_path, onerror=remove_readonly)

        print("\nResultado final:")

        print(json.dumps({
            "status": "success",
            "accessibility_report": accessibility_report
        }, indent=2))

    except Exception as e:

        print(json.dumps({
            "status": "error",
            "message": str(e)
        }))