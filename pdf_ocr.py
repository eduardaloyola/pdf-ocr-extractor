"""
pdf_ocr.py
----------
Converte um PDF salvo como imagem (escaneado) em texto,
usando PyMuPDF para leitura/renderizacao e EasyOCR para fallback de OCR.

Uso:
    python pdf_ocr.py [arquivo-ou-pasta] [opcoes]

Exemplos:
    python pdf_ocr.py documento.pdf
    python pdf_ocr.py PDF
    python pdf_ocr.py documento.pdf --lang por
    python pdf_ocr.py documento.pdf --lang por+eng --output resultado.txt
    python pdf_ocr.py PDF --output saida
    python pdf_ocr.py documento.pdf --dpi 400 --pages 1-3

"""

import argparse
import re
import sys
import time
import unicodedata
import warnings
from pathlib import Path

try:
    import easyocr
    import fitz  # PyMuPDF
    import numpy as np
except ImportError as e:
    print(f"[ERRO] Dependencia nao encontrada: {e}")
    print("Execute: pip install -r requirements.txt")
    sys.exit(1)


EASYOCR_LANG_MAP = {
    "por": "pt",
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "deu": "de",
    "ita": "it",
}

WHITESPACE_REGEX = re.compile(r"\s+")
_READER_CACHE = {}
NOISE_LINES = {"PAGINA", "NOTA SALVADOR", "PRESTADO", "SERVICOS"}
FIELD_STOP_WORDS = ["CPF", "CNPJ", "INSCRI", "ENDERE", "EMAIL", "TOMADOR", "PRESTADO"]

# Evita poluir a saida com aviso de pin_memory quando nao ha GPU.
warnings.filterwarnings(
    "ignore",
    message=".*pin_memory.*no accelerator is found.*",
    category=UserWarning,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_page_range(page_range, total_pages):
    """
    Converte uma string de intervalo como '1-3,5,7-9' em lista de indices
    base-0 validos para as paginas do PDF.
    """
    pages = set()
    for part in page_range.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(part))

    valid = sorted(p - 1 for p in pages if 1 <= p <= total_pages)
    if not valid:
        raise ValueError(
            f"Nenhuma pagina valida no intervalo '{page_range}'. "
            f"O PDF tem {total_pages} pagina(s)."
        )
    return valid


def parse_easyocr_languages(lang_value):
    """Converte o formato por+eng para os codigos usados pelo EasyOCR."""
    languages = []
    for item in lang_value.split("+"):
        code = item.strip().lower()
        if not code:
            continue

        mapped = EASYOCR_LANG_MAP.get(code, code)
        if mapped not in languages:
            languages.append(mapped)

    if not languages:
        raise ValueError("Nenhum idioma valido foi informado.")

    return languages


def get_easyocr_reader(languages):
    """Reaproveita o Reader por combinacao de idiomas para evitar recarga de modelo."""
    key = tuple(languages)
    reader = _READER_CACHE.get(key)
    if reader is None:
        reader = easyocr.Reader(languages, gpu=False, verbose=False)
        _READER_CACHE[key] = reader
    return reader


def render_page_to_numpy(page, dpi, max_pixels):
    """Renderiza uma pagina PDF para numpy limitando o total de pixels para acelerar OCR."""
    zoom = dpi / 72.0
    estimated_w = page.rect.width * zoom
    estimated_h = page.rect.height * zoom
    estimated_pixels = estimated_w * estimated_h

    if max_pixels and estimated_pixels > max_pixels:
        scale = (max_pixels / estimated_pixels) ** 0.5
        zoom *= scale

    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)


def run_ocr(page_arrays, languages, verbose):
    """Executa OCR em cada pagina (numpy array) e retorna lista de textos."""
    reader = get_easyocr_reader(languages)
    results = []
    total = len(page_arrays)

    for idx, img in enumerate(page_arrays, start=1):
        if verbose:
            print(f"  OCR pagina {idx}/{total}...", end="\r", flush=True)

        chunks = reader.readtext(img, detail=0, paragraph=False, decoder="greedy")
        text = "\n".join(chunk.strip() for chunk in chunks if chunk.strip())
        results.append(text)

    if verbose:
        print(f"  OCR concluido em {total} pagina(s).           ")

    return results


def build_output(texts, page_indices, total_pages):
    """
    Monta o texto final com separadores de pagina.
    """
    parts = []
    for i, text in enumerate(texts):
        page_num = (page_indices[i] + 1) if page_indices else (i + 1)
        separator = f"\n{'=' * 60}\n  PAGINA {page_num} / {total_pages}\n{'=' * 60}\n"
        parts.append(separator + text.strip())

    return "\n".join(parts)


def normalize_text(text):
    """Normaliza espacos sem perder quebras de linha estruturais."""
    lines = [WHITESPACE_REGEX.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def strip_accents(text):
    """Remove acentos para facilitar matching robusto em texto OCR."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_for_match(text):
    """Normaliza texto para matching case-insensitive e sem acentos."""
    text = strip_accents(text).upper()
    return WHITESPACE_REGEX.sub(" ", text).strip()


def next_non_empty_line(lines, start_idx):
    """Retorna a proxima linha nao vazia a partir de um indice."""
    for i in range(start_idx, len(lines)):
        line = lines[i].strip()
        if line:
            return line
    return ""


def extract_invoice_number(full_text):
    """Extrai o numero da nota, tolerando pequenas variacoes de OCR."""
    normalized = normalize_for_match(full_text)
    match = re.search(r"NUMERO\s+DA\s+NOTA\s*[:\-]?\s*([0-9]{4,})", normalized)
    if match:
        return match.group(1)

    # Fallback: pega o primeiro grupo numerico logo apos a frase-chave.
    match = re.search(r"NUMERO\s+DA\s+NOTA\s*[:\-]?\s*([^\n]{0,80})", normalized)
    if match:
        tail = match.group(1)
        digits = re.search(r"([0-9]{4,})", tail)
        if digits:
            return digits.group(1)

    return "nao encontrado"


def extract_invoice_amount(full_text):
    """Extrai o valor total da nota, tolerando variacoes comuns do OCR."""
    normalized = normalize_for_match(full_text)

    patterns = [
        r"VALOR\s+TOTAL\s+DA\s+NOTA\s*[=:]?\s*R\$\s*([0-9]+(?:[\.,][0-9]{2})?)",
        r"VALOR\s+TOTAL\s+DA\s+NOTA\s*[=:]?\s*([0-9]+(?:[\.,][0-9]{2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = match.group(1).replace(".", "").replace(",", ".")
            try:
                return f"R$ {float(value):.2f}".replace(".", ",")
            except ValueError:
                continue

    return "nao encontrado"


def extract_razsoes_sociais(lines):
    """Extrai razoes sociais, removendo prestador QUALYCOPY."""
    found = []
    for i, line in enumerate(lines):
        norm = normalize_for_match(line)
        if "RAZAO SOCIAL" not in norm and "NOME/RAZAO" not in norm and "NOMERAZAO" not in norm:
            continue

        # Tenta capturar na mesma linha apos ':' e concatena linhas seguintes do mesmo campo.
        first_chunk = ""
        if ":" in line:
            first_chunk = line.split(":", 1)[1].strip()

        chunks = []
        if first_chunk:
            chunks.append(first_chunk)

        for j in range(i + 1, min(i + 6, len(lines))):
            nxt = lines[j].strip()
            if not nxt:
                continue
            nxt_norm = normalize_for_match(nxt)
            if any(word in nxt_norm for word in FIELD_STOP_WORDS):
                break
            chunks.append(nxt)

        candidate = " ".join(chunks).strip()
        if not candidate:
            continue

        candidate_norm = normalize_for_match(candidate)
        if "QUALYCOPY" in candidate_norm:
            continue
        if any(token in candidate_norm for token in NOISE_LINES):
            continue
        if candidate not in found:
            found.append(candidate)

    return found


def extract_unit_and_address(lines):
    """Extrai unidade e endereco do bloco do tomador."""

    unit = "nao encontrado"
    address = "nao encontrado"

    norms = [normalize_for_match(ln) for ln in lines]

    tomador_idx = next(
        (
            i
            for i, n in enumerate(norms)
            if "TOMADOR" in n and "SERVI" in n
        ),
        0,
    )

    block_end = min(len(lines), tomador_idx + 60)

    # ==========================
    # UNIDADE
    # ==========================
    for i in range(tomador_idx, block_end):

        joined = " ".join(
            norms[i:min(i + 5, block_end)]
        )

        # Procura o padrão "UNIDADE ..."
        match = re.search(
            r"UNIDADE\s+([A-Z0-9\s]+)",
            joined,
        )

        if match:

            unidade = match.group(1)

            # Remove palavras que podem aparecer depois
            unidade = re.split(
                r"(COMPETENCIA|ENDERE|EMAIL|CPF|CNPJ)",
                unidade
            )[0]

            unidade = unidade.strip()

            if unidade:
                unit = unidade
                break

    # ==========================
    # ENDEREÇO
    # ==========================
    for i in range(tomador_idx, block_end):

        n = norms[i]

        if "ENDERE" not in n:
            continue

        candidate_parts = []

        for j in range(i + 1, min(i + 5, block_end)):

            raw = lines[j].strip()

            if not raw:
                continue

            raw_norm = norms[j]

            if any(
                stop in raw_norm
                for stop in [
                    "EMAIL",
                    "CPF",
                    "CNPJ",
                    "INSCRI",
                    "DISCRIMINAC",
                    "DADOS PARA",
                ]
            ):
                break

            candidate_parts.append(raw)

        if candidate_parts:

            address = " ".join(candidate_parts)

            break

    return unit, address


def build_filtered_output(full_text):
    """Monta saida enxuta com os campos solicitados pelo usuario."""
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    note_number = extract_invoice_number(full_text)
    amount = extract_invoice_amount(full_text)
    razoes = extract_razsoes_sociais(lines)
    print("\n===== LINHAS OCR =====")
    for i, linha in enumerate(lines):
     print(f"{i}: {linha}")
     print("======================\n")
     unit, address = extract_unit_and_address(lines)

    output_lines = [
        f"Numero da Nota: {note_number}",
        f"Valor da Nota: {amount}",
        f"Unidade: {unit}",
        f"Endereco: {address}",
        "Razoes Sociais:",
    ]

    if razoes:
        output_lines.extend(f"- {name}" for name in razoes)
    else:
        output_lines.append("- nao encontrado")

    return "\n".join(output_lines)


def resolve_pdf_inputs(input_path):
    """Resolve um arquivo PDF unico ou uma pasta contendo PDFs."""
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError(f"O arquivo informado nao e um PDF: {input_path.name}")
        return [input_path]

    if input_path.is_dir():
        pdf_files = sorted(path for path in input_path.iterdir() if path.suffix.lower() == ".pdf")
        if not pdf_files:
            raise ValueError(f"Nenhum arquivo PDF encontrado em: {input_path}")
        return pdf_files

    raise ValueError(f"Caminho nao encontrado: {input_path}")


def resolve_output_path(pdf_path, source_path, output_arg):
    """Define o caminho de saida para cada PDF processado."""
    if output_arg is None:
        return pdf_path.with_suffix(".txt")

    output_path = Path(output_arg)
    if source_path.is_dir() or output_path.suffix.lower() != ".txt":
        output_path.mkdir(parents=True, exist_ok=True)
        return output_path / f"{pdf_path.stem}.txt"

    return output_path


def resolve_batch_output_path(source_path, output_arg):
    """Define o arquivo consolidado para processamento em pasta."""
    if output_arg is None:
        return source_path / "resultado.txt"

    output_path = Path(output_arg)
    if output_path.exists() and output_path.is_dir():
        return output_path / "resultado.txt"

    if output_path.suffix.lower() == ".txt":
        return output_path

    return output_path.with_suffix(".txt")


def process_pdf(pdf_path, args, verbose, source_path, output_path=None):
    """Extrai texto completo de um unico PDF com fallback para OCR."""
    if output_path is None and source_path.is_file():
        output_path = resolve_output_path(pdf_path, source_path, args.output)

    if verbose:
        print(f"\n{'─' * 60}")
        print(f"  Arquivo  : {pdf_path}")
        print(f"  Idioma   : {args.lang}")
        print(f"  DPI      : {args.dpi}")
        if output_path:
            print(f"  Saida    : {output_path}")
        print(f"{'─' * 60}\n")

    start = time.time()

    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    if verbose:
        print(f"Total de paginas: {total_pages}")

    page_indices = None
    if args.pages:
        page_indices = parse_page_range(args.pages, total_pages)
        if verbose:
            nums = [i + 1 for i in page_indices]
            print(f"Processando paginas: {nums}")
    else:
        page_indices = list(range(total_pages))

    ocr_languages = parse_easyocr_languages(args.lang)

    direct_texts = []
    ocr_page_arrays = []
    ocr_positions = []

    for i in page_indices:
        page = doc.load_page(i)
        text = normalize_text(page.get_text("text"))
        # Se ja houver texto suficiente no PDF, evita OCR para ganhar velocidade.
        if len(text) >= 40:
            direct_texts.append(text)
            continue
        direct_texts.append("")
        ocr_positions.append(len(direct_texts) - 1)
        ocr_page_arrays.append(render_page_to_numpy(page, args.dpi, args.max_pixels))

    if ocr_page_arrays:
        if verbose:
            print(f"\nExecutando OCR em {len(ocr_page_arrays)} pagina(s) com idiomas {ocr_languages}...")
        ocr_texts = run_ocr(ocr_page_arrays, languages=ocr_languages, verbose=verbose)
        for pos, text in zip(ocr_positions, ocr_texts):
            direct_texts[pos] = normalize_text(text)

    full_text = build_output(direct_texts, page_indices, total_pages)
    final_output = full_text if args.full_text else build_filtered_output(full_text)
    if output_path:
        output_path.write_text(final_output + "\n", encoding="utf-8")

    elapsed = time.time() - start
    doc.close()

    if verbose:
        print(f"\n{'─' * 60}")
        if args.full_text:
            print("  Texto completo extraido com sucesso!")
            print(f"  Caracteres: {len(final_output)}")
        else:
            print("  Campos filtrados extraidos com sucesso!")
        print(f"  Tempo     : {elapsed:.1f}s")
        if output_path:
            print(f"  Salvo em  : {output_path}")
        print(f"{'─' * 60}\n")

    return final_output, output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extrai texto completo de PDF usando leitura direta + OCR rapido quando necessario."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        type=str,
        default="PDF",
        help="Caminho para um arquivo PDF ou para uma pasta com PDFs. Padrao: pasta 'PDF'.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="por",
        help=(
            "Idioma(s) do OCR separados por '+'. "
            "Ex.: 'por', 'eng', 'por+eng'. Padrao: 'por'."
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=170,
        help="Resolucao para OCR em paginas sem texto embutido. Padrao: 170 (mais rapido).",
    )
    parser.add_argument(
        "--max-pixels",
        type=int,
        default=2200000,
        help=(
            "Limite de pixels por pagina no OCR para acelerar processamento. "
            "Padrao: 2200000. Menor = mais rapido."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Arquivo ou pasta de saida. Para um PDF unico, aceita um .txt especifico. "
            "Para pasta de PDFs, gera um resultado consolidado (padrao: resultado.txt)."
        ),
    )
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Paginas a processar. Ex.: '1', '1-3', '1,3,5-7'. Padrao: todas.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suprime mensagens de progresso.",
    )
    parser.add_argument(
        "--full-text",
        action="store_true",
        help="Salva o texto completo extraido. Sem esta flag, salva somente os campos filtrados.",
    )

    args = parser.parse_args()
    verbose = not args.quiet

    # --- Valida o arquivo de entrada ---
    source_path = Path(args.pdf)
    try:
        pdf_files = resolve_pdf_inputs(source_path)
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)

    if source_path.is_dir():
        batch_output = resolve_batch_output_path(source_path, args.output)
        if verbose:
            print(f"[INFO] Pasta detectada. Processando {len(pdf_files)} PDF(s).")

        sections = []
        for pdf_path in pdf_files:
            try:
                result_text, _ = process_pdf(pdf_path, args, verbose, source_path, output_path=None)
                section = f"Arquivo: {pdf_path.name}\n{result_text.strip()}"
                sections.append(section)
            except ValueError as e:
                print(f"[ERRO] {e}")
                sys.exit(1)
            except Exception as e:
                print(f"\n[ERRO] Falha ao processar '{pdf_path.name}': {e}")
                print("[INFO] O EasyOCR baixa os modelos na primeira execucao e pode demorar um pouco mais.")
                sys.exit(1)

        batch_output.write_text("\n----\n".join(sections) + "\n", encoding="utf-8")
        if verbose:
            print(f"[INFO] Resultado consolidado salvo em: {batch_output}")
    else:
        for pdf_path in pdf_files:
            try:
                process_pdf(pdf_path, args, verbose, source_path)
            except ValueError as e:
                print(f"[ERRO] {e}")
                sys.exit(1)
            except Exception as e:
                print(f"\n[ERRO] Falha ao processar '{pdf_path.name}': {e}")
                print("[INFO] O EasyOCR baixa os modelos na primeira execucao e pode demorar um pouco mais.")
                sys.exit(1)


if __name__ == "__main__":
    main()