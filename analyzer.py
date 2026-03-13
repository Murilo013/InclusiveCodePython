import sys
import json
import os
import shutil
import tempfile
import subprocess
import stat
import time
import re
from google import genai
from google.genai import types
from dotenv import load_dotenv
from openai import OpenAI


def log(msg):
    print(msg, file=sys.stderr)


def clone_repo(repo_url):
    temp_dir = tempfile.mkdtemp()

    log("Clonando repositório...")

    subprocess.run(
        ["git", "clone", repo_url, temp_dir],
        check=True
    )

    return temp_dir


def read_web_files(repo_path):
    web_files = []

    for root, dirs, files in os.walk(repo_path):
        for file in files:

            if file.lower().endswith((".html", ".jsx", ".tsx", ".css", ".php")):
                file_path = os.path.join(root, file)

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    relative_path = os.path.relpath(file_path, repo_path)

                    web_files.append({
                        "filename": relative_path,
                        "content": content
                    })

                except Exception:
                    log(f"Erro lendo arquivo: {file_path}")

    return web_files


def extract_json_from_text(text):
    """
    Tenta extrair JSON de uma resposta que pode conter texto extra
    ou markdown ```json
    """

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

    client = genai.Client(api_key=api_key)

    system_prompt = """
Você é um auditor de acessibilidade.

Responda em Português.

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
      "line": number,
      "snippet": "string",
            "issue": "string",
            "improvement": "string"
    }
  ]
}

Regras obrigatórias para cada item em issues:
- descreva a falha em issue
- em improvement, retorne o trecho de código corrigido para aquela falha específica
- improvement deve ser um patch local do erro, sem texto explicativo extra
"""

    prompt_parts = [system_prompt, "\nFiles:\n"]

    for f in web_files:
        prompt_parts.append(
            f"\nArquivo: {f['filename']}\nCodigo:\n{f['content']}\n"
        )

    prompt = "\n".join(prompt_parts)

    log("Enviando requisição para Gemini...")

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=8192,
            response_schema={
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
                                "issue": {"type": "string"},
                                "improvement": {"type": "string"}
                            },
                            "required": ["filename", "snippet", "issue", "improvement"]
                        }
                    }
                },
                "required": ["summary", "non_conforming_count", "issues"]
            }
        )
    )

    text = response.text

    # tenta converter diretamente
    try:
        parsed = json.loads(text)
        return parsed

    except json.JSONDecodeError:
        log("JSON direto falhou, tentando extrair JSON...")

    parsed = extract_json_from_text(text)

    if parsed:
        return parsed

    log("Resposta da IA não é JSON válido")

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

        log(f"Arquivos encontrados: {len(web_files)}")

        accessibility_report = analyze_accessibility_tags(
            web_files,
            confirm_mode
        )

        time.sleep(1)

        shutil.rmtree(repo_path, onerror=remove_readonly)

        result = {
            "status": "success",
            "accessibility_report": accessibility_report
        }

        # JSON final vai para stdout
        print(json.dumps(result, indent=2))

    except Exception as e:

        error_result = {
            "status": "error",
            "message": str(e)
        }

        print(json.dumps(error_result))