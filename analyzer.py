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


def is_web_repository(web_files):
    return len(web_files) > 0


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


GENERIC_ALT_VALUES = {
    "image",
    "img",
    "photo",
    "picture",
    "imagem",
    "foto",
    "logo",
    "banner"
}

GENERIC_LINK_OR_BUTTON_TEXTS = {
    "click here",
    "clique aqui",
    "saiba mais",
    "read more",
    "more",
    "link"
}


def _attr_value(tag, attr_name):
    match = re.search(
        rf"\b{re.escape(attr_name)}\s*=\s*['\"](.*?)['\"]",
        tag,
        re.IGNORECASE | re.DOTALL
    )

    if not match:
        return None

    return match.group(1).strip()


def _normalize_spaces(text):
    return re.sub(r"\s+", " ", text).strip()


def _strip_html_tags(text):
    return _normalize_spaces(re.sub(r"<[^>]+>", " ", text))


def _line_number_from_index(text, index):
    return text.count("\n", 0, index) + 1


def _line_content_from_index(text, index):
    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index)

    if line_end == -1:
        line_end = len(text)

    return text[line_start:line_end]


def _extract_element_snippet(text, start_index, tag_name):
    opening_end = text.find(">", start_index)

    if opening_end == -1:
        return ""

    opening_tag = text[start_index:opening_end + 1]

    if opening_tag.rstrip().endswith("/>") or tag_name.lower() in {"img", "input", "br", "hr", "meta", "link"}:
        return _normalize_spaces(opening_tag)

    closing_match = re.search(
        rf"</{re.escape(tag_name)}\s*>",
        text[opening_end + 1:],
        re.IGNORECASE
    )

    if not closing_match:
        return _normalize_spaces(opening_tag)

    closing_end = opening_end + 1 + closing_match.end()
    return _normalize_spaces(text[start_index:closing_end])


def _is_generic_alt(alt_text):
    normalized = _normalize_spaces(alt_text).lower()
    return normalized in GENERIC_ALT_VALUES


def _parse_hex_color(color_text):
    color = color_text.strip().lstrip("#")

    if len(color) == 3:
        color = "".join(c * 2 for c in color)

    if len(color) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", color):
        return None

    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(rgb):
    def _channel(value):
        normalized = value / 255
        if normalized <= 0.03928:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    r, g, b = rgb

    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(color_a, color_b):
    rgb_a = _parse_hex_color(color_a)
    rgb_b = _parse_hex_color(color_b)

    if rgb_a is None or rgb_b is None:
        return None

    l1 = _relative_luminance(rgb_a)
    l2 = _relative_luminance(rgb_b)

    lighter = max(l1, l2)
    darker = min(l1, l2)

    return (lighter + 0.05) / (darker + 0.05)


def _collect_problematic_tags_for_file(filename, content):
    issues = []

    label_for_ids = set(
        match.group(1).strip()
        for match in re.finditer(
            r"<label\b[^>]*\bfor\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
            content,
            re.IGNORECASE
        )
    )

    heading_tags = []

    for match in re.finditer(r"<img\b[^>]*>", content, re.IGNORECASE):
        tag = match.group(0)
        alt_text = _attr_value(tag, "alt")

        if alt_text is None or alt_text == "" or _is_generic_alt(alt_text):
            issues.append({
                "filename": filename,
                "line": _line_number_from_index(content, match.start()),
                "snippet": _extract_element_snippet(content, match.start(), "img")
            })

    for match in re.finditer(r"<input\b[^>]*>", content, re.IGNORECASE):
        tag = match.group(0)
        input_type = (_attr_value(tag, "type") or "").lower()

        if input_type == "hidden":
            continue

        line_number = _line_number_from_index(content, match.start())
        line = _line_content_from_index(content, match.start())
        aria_label = _attr_value(tag, "aria-label")
        aria_labelledby = _attr_value(tag, "aria-labelledby")
        input_id = _attr_value(tag, "id")
        has_wrapping_label_in_line = "<label" in line.lower()
        has_associated_label = bool(input_id and input_id in label_for_ids)

        if (
            not has_wrapping_label_in_line
            and not has_associated_label
            and not (aria_label and aria_label.strip())
            and not (aria_labelledby and aria_labelledby.strip())
        ):
            issues.append({
                "filename": filename,
                "line": line_number,
                "snippet": _extract_element_snippet(content, match.start(), "input")
            })

    for match in re.finditer(r"<(div|span)\b[^>]*>", content, re.IGNORECASE):
        tag_name = match.group(1)
        tag = match.group(0)

        if re.search(r"\bonclick\s*=", tag, re.IGNORECASE):
            issues.append({
                "filename": filename,
                "line": _line_number_from_index(content, match.start()),
                "snippet": _extract_element_snippet(content, match.start(), tag_name)
            })

    for heading in re.finditer(r"<h([1-6])\b[^>]*>", content, re.IGNORECASE):
        heading_level = int(heading.group(1))
        heading_tags.append((
            _line_number_from_index(content, heading.start()),
            heading_level,
            _extract_element_snippet(content, heading.start(), f"h{heading_level}")
        ))

    for element in ("a", "button"):
        pattern = rf"<{element}\b[^>]*>(.*?)</{element}>"

        for match in re.finditer(pattern, content, re.IGNORECASE | re.DOTALL):
            tag = _normalize_spaces(match.group(0))
            inner_text = _strip_html_tags(match.group(1)).lower()
            aria_label = _attr_value(tag, "aria-label")
            title = _attr_value(tag, "title")
            has_accessible_name = bool(
                (aria_label and aria_label.strip())
                or (title and title.strip())
                or inner_text
            )

            if not has_accessible_name or inner_text in GENERIC_LINK_OR_BUTTON_TEXTS:
                issues.append({
                    "filename": filename,
                    "line": _line_number_from_index(content, match.start()),
                    "snippet": tag
                })

    for match in re.finditer(
        r"<([a-zA-Z][\w:-]*)[^>]*\bstyle\s*=\s*['\"][^'\"]*['\"][^>]*>",
        content,
        re.IGNORECASE
    ):
        tag_name = match.group(1)
        opening_tag = match.group(0)
        style = _attr_value(opening_tag, "style")

        if not style:
            continue

        color_match = re.search(
            r"(?:^|;)\s*color\s*:\s*(#[0-9a-fA-F]{3,6})\b",
            style,
            re.IGNORECASE
        )
        background_match = re.search(
            r"(?:^|;)\s*(?:background|background-color)\s*:\s*(#[0-9a-fA-F]{3,6})\b",
            style,
            re.IGNORECASE
        )

        if color_match and background_match:
            ratio = _contrast_ratio(
                color_match.group(1),
                background_match.group(1)
            )

            if ratio is not None and ratio < 4.5:
                issues.append({
                    "filename": filename,
                    "line": _line_number_from_index(content, match.start()),
                    "snippet": _extract_element_snippet(content, match.start(), tag_name)
                })

    if heading_tags:
        first_line, first_level, first_snippet = heading_tags[0]

        if first_level != 1:
            issues.append({
                "filename": filename,
                "line": first_line,
                "snippet": first_snippet
            })

        previous_level = first_level

        for line_number, level, snippet in heading_tags[1:]:
            if level - previous_level > 1:
                issues.append({
                    "filename": filename,
                    "line": line_number,
                    "snippet": snippet
                })

            previous_level = level

    deduped = []
    seen = set()

    for issue in issues:
        key = (issue["filename"], issue["line"], issue["snippet"])
        if key in seen:
            continue

        seen.add(key)
        deduped.append(issue)

    return deduped


def collect_problematic_tags(web_files):
    all_issues = []

    for web_file in web_files:
        issues = _collect_problematic_tags_for_file(
            web_file["filename"],
            web_file["content"]
        )
        all_issues.extend(issues)

    return all_issues


def format_tag_collection_for_prompt(collected_tags):
    if not collected_tags:
        return "Nenhuma tag potencialmente problemática encontrada."

    lines = []
    current_file = None

    for tag in collected_tags:
        filename = tag["filename"]

        if filename != current_file:
            if lines:
                lines.append("")
            lines.append(f"Arquivo: {filename}")
            current_file = filename

        lines.append(f"- Linha {tag['line']}: {tag['snippet']}")

    return "\n".join(lines)


def _usage_field(usage_metadata, snake_name, camel_name=None):
    if usage_metadata is None:
        return None

    if isinstance(usage_metadata, dict):
        if snake_name in usage_metadata:
            return usage_metadata[snake_name]
        if camel_name and camel_name in usage_metadata:
            return usage_metadata[camel_name]
        return None

    value = getattr(usage_metadata, snake_name, None)
    if value is not None:
        return value

    if camel_name:
        return getattr(usage_metadata, camel_name, None)

    return None


def log_token_usage(response):
    usage_metadata = getattr(response, "usage_metadata", None)

    if usage_metadata is None:
        usage_metadata = getattr(response, "usageMetadata", None)

    if usage_metadata is None:
        log("Uso de tokens não disponível na resposta.")
        return

    prompt_tokens = _usage_field(
        usage_metadata,
        "prompt_token_count",
        "promptTokenCount"
    )
    output_tokens = _usage_field(
        usage_metadata,
        "candidates_token_count",
        "candidatesTokenCount"
    )
    total_tokens = _usage_field(
        usage_metadata,
        "total_token_count",
        "totalTokenCount"
    )

    log(
        "Uso de tokens (Gemini): "
        f"prompt={prompt_tokens if prompt_tokens is not None else 'N/A'}, "
        f"output={output_tokens if output_tokens is not None else 'N/A'}, "
        f"total={total_tokens if total_tokens is not None else 'N/A'}"
    )


def analyze_accessibility_tags(web_files, confirm_mode=False):

    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY_SECONDARY")

    if not api_key:
        raise Exception("GEMINI_API_KEY_SECONDARY não encontrada no .env")

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
      "severity": "CRÍTICO | MODERADO | BAIXO",
            "improvement": "string",
            "evidence_urls": ["string"]
    }
  ]
}

Regras obrigatórias para cada item em issues:
- descreva a falha em issue
- classifique a gravidade em severity usando exatamente um dos valores: CRÍTICO, MODERADO ou BAIXO
  - CRÍTICO: impede completamente o uso por pessoas com deficiência (ex: imagem sem alt, input sem label)
  - MODERADO: dificulta significativamente o uso, mas há contorno parcial (ex: hierarquia incorreta de headings)
  - BAIXO: má prática que tem impacto menor na experiência acessível (ex: link com texto genérico)
  - em improvement, retorne o trecho de código corrigido para aquela falha específica
  - improvement deve ser um patch local do erro, sem texto explicativo extra
  - inclua um campo `evidence_urls`: lista de URLs usadas como referência/evidência para formar a resposta; se não houver, retorne uma lista vazia
"""

    collected_tags = collect_problematic_tags(web_files)
    formatted_tags = format_tag_collection_for_prompt(collected_tags)

    log(f"Tags potencialmente problemáticas coletadas: {len(collected_tags)}")

    prompt = "\n".join([
        "\nTags candidatas para auditoria:\n",
        formatted_tags,
        ""
    ])

    log("System instruction configurada nativamente no request.")
    log("===== INICIO DO ENVIO PARA IA =====")
    log(prompt)
    log("===== FIM DO ENVIO PARA IA =====")

    log("Enviando requisição para Gemini...")

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
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
                                "severity": {
                                    "type": "string",
                                    "enum": ["CRÍTICO", "MODERADO", "BAIXO"]
                                },
                                "improvement": {"type": "string"}
                                ,
                                "evidence_urls": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                }
                            },
                            "required": ["filename", "snippet", "issue", "severity", "improvement", "evidence_urls"]
                        }
                    }
                },
                "required": ["summary", "non_conforming_count", "issues"]
            }
        )
    )

    log_token_usage(response)

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

        if not is_web_repository(web_files):
            shutil.rmtree(repo_path, onerror=remove_readonly)
            error_result = {
                "status": "error",
                "message": "erro de analise, repositorio nao corresponde a uma pagina web"
            }
            print(json.dumps(error_result))
            sys.exit(1)

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